"""Contains the internal repository rule version_repo."""

def _version_repo_impl(rctx):
    rctx.file(
        "BUILD.bazel",
        """
load("@bazel_skylib//:bzl_library.bzl", "bzl_library")

exports_files(["version.bzl"])

bzl_library(
    name = "version",
    srcs = ["version.bzl"],
    visibility = ["//visibility:public"],
)
""",
    )
    # Bazel 7 compat: the system Bazel 7 installation reports an empty
    # native.bazel_version, which upstream parse_version() treats as a dev
    # version greater than anything, enabling Bazel 8+ feature detection on
    # Bazel 7. Pin the actual version instead.
    rctx.file("version.bzl", "version = '" + (native.bazel_version or "7.7.1") + "'")

version_repo = repository_rule(
    _version_repo_impl,
    # Force reruns on server restarts to keep native.bazel_version up-to-date.
    local = True,
)
