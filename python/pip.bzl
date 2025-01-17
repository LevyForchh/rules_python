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
"""Import pip requirements into Bazel."""

def _pip_import_impl(repository_ctx):
    """Core implementation of pip_import."""

    # Add an empty top-level BUILD file.
    # This is because Bazel requires BUILD files along all paths accessed
    # via //this/sort/of:path and we wouldn't be able to load our generated
    # requirements.bzl without it.
    repository_ctx.file("BUILD", "")

    interpreter_path = repository_ctx.attr.python_interpreter
    if repository_ctx.attr.python_interpreter_target != None:
        target = repository_ctx.attr.python_interpreter_target
        interpreter_path = repository_ctx.path(target)

    args = [
        interpreter_path,
        repository_ctx.path(repository_ctx.attr._script),
        "--python_interpreter",
        interpreter_path,
        "--name",
        repository_ctx.attr.name,
        "--input",
        repository_ctx.path(repository_ctx.attr.requirements),
        "--output",
        repository_ctx.path("requirements.bzl"),
        "--directory",
        repository_ctx.path(""),
    ]
    if repository_ctx.attr.extra_pip_args:
        args += [
            "--extra_pip_args",
            "\"" + " ".join(repository_ctx.attr.extra_pip_args) + "\"",
        ]

    # To see the output, pass: quiet=False
    result = repository_ctx.execute(args, quiet=repository_ctx.attr.quiet, timeout=repository_ctx.attr.timeout)

    if result.return_code:
        fail("pip_import failed: %s (%s)" % (result.stdout, result.stderr))

pip_import = repository_rule(
    attrs = {
        "extra_pip_args": attr.string_list(
            doc = "Extra arguments to pass on to pip. Must not contain spaces.",
        ),
        "python_interpreter": attr.string(default = "python", doc = """
The command to run the Python interpreter used to invoke pip and unpack the
wheels.
"""),
        "python_interpreter_target": attr.label(allow_single_file = True, doc = """
If you are using a custom python interpreter built by another repository rule,
use this attribute to specify its BUILD target. This allows pip_import to invoke
pip using the same interpreter as your toolchain. If set, takes precedence over
python_interpreter.
"""),
        "quiet": attr.bool(
            default = True,
            doc = "Silence the output of the pip commands."
        ),
        "requirements": attr.label(
            mandatory = True,
            allow_single_file = True,
            doc = "The label of the requirements.txt file.",
        ),
        "timeout": attr.int(
            default = 600,
            doc = "Timeout (in seconds) for repository fetch."
        ),
        "_script": attr.label(
            executable = True,
            default = Label("//tools:piptool.par"),
            cfg = "host",
        ),
    },
    implementation = _pip_import_impl,
    doc = """A rule for importing `requirements.txt` dependencies into Bazel.

This rule imports a `requirements.txt` file and generates a new
`requirements.bzl` file.  This is used via the `WORKSPACE` pattern:

```python
pip_import(
    name = "foo",
    requirements = ":requirements.txt",
)
load("@foo//:requirements.bzl", "pip_install")
pip_install()
```

You can then reference imported dependencies from your `BUILD` file with:

```python
load("@foo//:requirements.bzl", "requirement")
py_library(
    name = "bar",
    ...
    deps = [
       "//my/other:dep",
       requirement("futures"),
       requirement("mock"),
    ],
)
```

Or alternatively:
```python
load("@foo//:requirements.bzl", "requirements")
py_binary(
    name = "baz",
    ...
    deps = [
       ":foo",
    ] + requirements.values(),
)
```
""",
)

# We don't provide a `pip2_import` that would use the `python2` system command
# because this command does not exist on all platforms. On most (but not all)
# systems, `python` means Python 2 anyway. See also #258.

def pip3_import(**kwargs):
    """A wrapper around pip_import that uses the `python3` system command.

    Use this for requirements of PY3 programs.
    """
    pip_import(python_interpreter = "python3", **kwargs)

def pip_repositories():
    """Pull in dependencies needed to use the packaging rules."""

    # At the moment this is a placeholder, in that it does not actually pull in
    # any dependencies. However, it does do some validation checking.
    #
    # As a side effect of migrating our canonical workspace name from
    # "@io_bazel_rules_python" to "@rules_python" (#203), users who still
    # imported us by the old name would get a confusing error about a
    # repository dependency cycle in their workspace. (The cycle is likely
    # related to the fact that our repo name is hardcoded into the template
    # in piptool.py.)
    #
    # To produce a more informative error message in this situation, we
    # fail-fast here if we detect that we're not being imported by the new
    # name. (I believe we have always had the requirement that we're imported
    # by the canonical name, because of the aforementioned hardcoding.)
    #
    # Users who, against best practice, do not call pip_repositories() in their
    # workspace will not benefit from this check.
    if "rules_python" not in native.existing_rules():
        message = "=" * 79 + """\n\
It appears that you are trying to import rules_python without using its
canonical name, "@rules_python". This does not work. Please change your
WORKSPACE file to import this repo with `name = "rules_python"` instead.
"""
        if "io_bazel_rules_python" in native.existing_rules():
            message += """\n\
Note that the previous name of "@io_bazel_rules_python" is no longer used.
See https://github.com/bazelbuild/rules_python/issues/203 for context.
"""
        message += "=" * 79
        fail(message)
