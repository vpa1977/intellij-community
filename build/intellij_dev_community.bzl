"""Macros for IntelliJ-based IDE development builds."""

load("@jps_dynamic_deps_community//:targets.bzl", "ALL_LIBRARY_COMMUNITY_TARGETS", "ALL_PRODUCTION_COMMUNITY_TARGETS", "BAZEL_TARGETS_JSON_COMMUNITY")
load("@rules_java//java:defs.bzl", "java_binary")
load(":intellij_dev.bzl", "intellij_dev_binary")
load("@bazel_features//:features.bzl", "bazel_features")

def _fallback_macro(inherit_attrs = None, attrs = {}, implementation = None, doc = None):
    def _wrapper(name, **kwargs):
        call_kwargs = dict(kwargs)
        for k, a in attrs.items():
            if k not in call_kwargs:
                print ("!!! " + str(a) + " === " + type(a))
                if type(a) in ["string", "bool", "int", "list"]:
                    call_kwargs[k] = a
                elif str(a) in ["<attr.string>"]:
                    call_kwargs[k] = ""
                elif str(a) in ["<attr.string_list>"]:
                    call_kwargs[k] = []
                elif str(a) in ["<attr.string_dict>"]:
                    call_kwargs[k] = {}
                elif hasattr(a, "default"):
                    call_kwargs[k] = a.default
                elif k.endswith("_enabled"):
                    call_kwargs[k] = True
                elif k in ["copts", "linkopts", "srcs", "hdrs", "deps", "data", "features", "jvm_flags"]:
                    call_kwargs[k] = []
        if "visibility" not in call_kwargs:
            call_kwargs["visibility"] = ["//visibility:public"]
        if "bazel_targets_json" not in call_kwargs:
            call_kwargs["bazel_targets_json"] = BAZEL_TARGETS_JSON_COMMUNITY
        print("WARNING: !!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!")
        print(*call_kwargs)
        print(attrs.items())
        print("WARNING: !!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!")
        return implementation(name = name, **call_kwargs)
    return _wrapper

_macro = getattr(bazel_features.globals, "macro", None)
macro = _macro if _macro != None else _fallback_macro

intellij_dev_binary_community = macro(
    doc = """Macro for IDEA-based dev-build targets.

    Creates a dev launcher for IntelliJ IDEA-based products using
    the DevMainKt entry point from the dev server.
    """,
    implementation = intellij_dev_binary,
    attrs = {
        "data": attr.label_list(default = ALL_PRODUCTION_COMMUNITY_TARGETS + ALL_LIBRARY_COMMUNITY_TARGETS, doc = "Data dependencies. Defaults to ALL_PRODUCTION_COMMUNITY_TARGETS + ALL_LIBRARY_COMMUNITY_TARGETS."),
        "jvm_flags": attr.string_list(default = [], doc = "Additional JVM flags."),
        "env": attr.string_dict(default = {},  doc = "Environment variables to set when running the binary."),
        "platform_prefix": attr.string(default = "", doc = "Value for -Didea.platform.prefix (e.g., 'idea', 'GoLand')."),
        "bazel_targets_json": attr.label(default = BAZEL_TARGETS_JSON_COMMUNITY, allow_single_file = True,  doc = "bazel-targets.json generated from JPS project model (community)"),
        "config_path": attr.string( default="", doc = "Path for -Didea.config.path. Defaults to out/dev-data/{name}/config."),
        "system_path": attr.string( default="", doc = "Path for -Didea.system.path. Defaults to out/dev-data/{name}/system."),
        "additional_modules": attr.string(default = "", doc = "Value for -Dadditional.modules flag (optional)."),
        "program_args": attr.string_list(default = [],  doc = "Value for program arguments (optional)."),
    },
)
