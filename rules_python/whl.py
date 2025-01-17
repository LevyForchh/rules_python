# Copyright 2017 The Bazel Authors. All rights reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#    http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
"""The whl modules defines classes for interacting with Python packages."""

import argparse
import email.parser
import os
import packaging.markers
import packaging.requirements
import re
import stat
import zipfile


def current_umask():
    """Get the current umask which involves having to set it temporarily."""
    mask = os.umask(0)
    os.umask(mask)
    return mask


def set_extracted_file_to_default_mode_plus_executable(path):
    """
    Make file present at path have execute for user/group/world
    (chmod +x) is no-op on windows per python docs
    """
    os.chmod(path, (0o777 & ~current_umask() | 0o111))


class Wheel(object):

  def __init__(self, path):
    self._path = path
    self._metadata = None

  def path(self):
    return self._path

  def basename(self):
    return os.path.basename(self.path())

  def distribution(self):
    # See https://www.python.org/dev/peps/pep-0427/#file-name-convention
    parts = self.basename().split('-')
    return parts[0]

  def version(self):
    # See https://www.python.org/dev/peps/pep-0427/#file-name-convention
    parts = self.basename().split('-')
    return parts[1]

  def repository_suffix(self):
    # Returns a canonical suffix that will form part of the name of the Bazel
    # repository for this package.
    canonical = 'pypi__{}_{}'.format(self.distribution(), self.version())
    # Escape any illegal characters with underscore.
    return re.sub('[-.+]', '_', canonical)

  def _dist_info(self):
    # Return the name of the dist-info directory within the .whl file.
    # e.g. google_cloud-0.27.0-py2.py3-none-any.whl ->
    #      google_cloud-0.27.0.dist-info
    return '{}-{}.dist-info'.format(self.distribution(), self.version())

  def metadata(self):
    if self._metadata == None:
      # Extract the structured data from METADATA in the WHL's dist-info
      # directory.
      with zipfile.ZipFile(self.path(), 'r') as whl:
        with whl.open(self._dist_info() + '/METADATA') as f:
          # Why are we using email.parser?
          #
          # From PEP-0314:
          #   The PKG-INFO file format is a single set of RFC-822 headers parseable by the rfc822.py module.
          #   The field names listed in the following section are used as the header names.
          #
          # The rfc822.py module has been deprecated since version 2.3 in favor of the email package.
          parser = email.parser.Parser()
          self._metadata = parser.parsestr(f.read().decode('ascii', 'ignore'))

    return self._metadata

  def name(self):
    return self.metadata().get('Name')

  def dependencies(self, extra=None):
    """Access the dependencies of this Wheel.

    Args:
      extra: if specified, include the additional dependencies
            of the named "extra".

    Yields:
      the names of requirements from METADATA
    """
    environment = packaging.markers.default_environment()
    environment['extra'] = extra
    # TODO(nathanlawrence): Are we properly setting python_version for the configured python_interpreter?

    # TODO(mattmoor): Is there a schema to follow for this?
    dependency_set = set()

    requires_dist = self.metadata().get_all('Requires-Dist', [])
    for requirement in requires_dist:
      parsed_requirement = packaging.requirements.Requirement(requirement)

      if parsed_requirement.marker and not parsed_requirement.marker.evaluate(environment=environment):
        # The current environment does not match the provided PEP 508 marker,
        # so ignore this requirement.
        continue

      if parsed_requirement.extras:
        dependency_set.add('{name}[{extras}]'.format(
          name=parsed_requirement.name,
          extras=','.join(parsed_requirement.extras),
        ))
      else:
        dependency_set.add(parsed_requirement.name)

    return sorted(dependency_set)

  def extras(self):
    return set(self.metadata().get_all('Provides-Extra', []))

  def expand(self, directory):
    with zipfile.ZipFile(self.path(), "r", allowZip64=True) as whl:
        whl.extractall(directory)
        # The following logic is borrowed from Pip:
        # https://github.com/pypa/pip/blob/cc48c07b64f338ac5e347d90f6cb4efc22ed0d0b/src/pip/_internal/utils/unpacking.py#L240
        for info in whl.infolist():
            name = info.filename
            # Do not attempt to modify directories.
            if name.endswith("/") or name.endswith("\\"):
                continue
            mode = info.external_attr >> 16
            # if mode and regular file and any execute permissions for
            # user/group/world?
            if mode and stat.S_ISREG(mode) and mode & 0o111:
                name = os.path.join(directory, name)
                set_extracted_file_to_default_mode_plus_executable(name)


parser = argparse.ArgumentParser(
    description='Unpack a WHL file as a py_library.')

parser.add_argument('--whl', action='store',
                    help=('The .whl file we are expanding.'))

parser.add_argument('--requirements', action='store',
                    help='The pip_import from which to draw dependencies.')

parser.add_argument('--directory', action='store', default='.',
                    help='The directory into which to expand things.')

parser.add_argument('--extras', action='append',
                    help='The set of extras for which to generate library targets.')

def main():
  """
  Generate a BUILD file for an unzipped Wheel

  We allow for empty Python sources as for Wheels containing only compiled C code
  there may be no Python sources whatsoever (e.g. packages written in Cython: like `pymssql`).
  """

  args = parser.parse_args()
  whl = Wheel(args.whl)

  # Extract the files into the current directory
  whl.expand(args.directory)

  with open(os.path.join(args.directory, 'BUILD'), 'w') as f:
    f.write("""
package(default_visibility = ["//visibility:public"])

load("@rules_python//python:defs.bzl", "py_library")
load("{requirements}", "requirement")

py_library(
    name = "pkg",
    srcs = glob(["**/*.py"], allow_empty = True),
    data = glob(["**/*"], exclude=["**/*.py", "**/* *", "BUILD", "WORKSPACE"]),
    # This makes this directory a top-level in the python import
    # search path for anything that depends on this.
    imports = ["."],
    deps = [{dependencies}],
)
{extras}""".format(
  requirements=args.requirements,
  dependencies=','.join([
    'requirement("%s")' % d
    for d in whl.dependencies()
  ]),
  extras='\n\n'.join([
    """py_library(
    name = "{extra}",
    deps = [
        ":pkg",{deps}
    ],
)""".format(extra=extra,
            deps=','.join([
                'requirement("%s")' % dep
                for dep in whl.dependencies(extra)
            ]))
    for extra in args.extras or []
  ])))

if __name__ == '__main__':
  main()
