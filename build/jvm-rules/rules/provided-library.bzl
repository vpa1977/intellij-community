load("@rules_java//java:defs.bzl", "JavaInfo")
load("@rules_kotlin//kotlin/internal:defs.bzl", KotlinInfo = "KtJvmInfo")
load("@bazel_features//:features.bzl", "bazel_features")

visibility("private")

# rules_java creates empty JAR for each java_library target,
# so, we use rules_kotlin approach - use shared empty jar as output_jar (required attribute for JavaInfo)
def _jvm_provided_libraries(ctx):
    lib = ctx.attr.exports[0]

    resultJavaInfo = JavaInfo(
        output_jar = ctx.file._empty_jar,
        compile_jar = None,
        exports = [lib[JavaInfo]],
        neverlink = True,
    )

    kotlinInfo = lib[KotlinInfo] if KotlinInfo in lib else None
    if kotlinInfo and hasattr(kotlinInfo, "exported_compiler_plugins"):
        exported_compiler_plugins = kotlinInfo.exported_compiler_plugins
        if exported_compiler_plugins:
            return [resultJavaInfo, KotlinInfo(exported_compiler_plugins = exported_compiler_plugins)]

    return [resultJavaInfo, KotlinInfo(exported_compiler_plugins = depset())]

_rule = rule(
    attrs = {
        "exports": attr.label_list(
            doc = """exports attribute instead of a singular lib attribute. This way the Bazel plugin can recognize it""",
            mandatory = True,
            allow_files = False,
            providers = [[JavaInfo], [JavaInfo, KotlinInfo]],
        ),
        "_empty_jar": attr.label(
            doc = """Empty jar for exporting JavaInfos.""",
            allow_single_file = True,
            default = Label("@rules_kotlin//third_party:empty.jar"),
        ),
    },
    provides = [JavaInfo, KotlinInfo],
    implementation = _jvm_provided_libraries,
)

def _jvm_provided_library(name, visibility, lib):
    return _rule(
        name = name,
        visibility = visibility,
        exports = [lib],
    )

def _fallback_macro(inherit_attrs = None, attrs = {}, implementation = None):
    def _wrapper(name, **kwargs):
        call_kwargs = dict(kwargs)
        for k, a in attrs.items():
            if k not in call_kwargs:
                if type(a) in ["string", "bool", "int", "list"]:
                    call_kwargs[k] = a
                elif hasattr(a, "default"):
                    call_kwargs[k] = a.default
                elif k.endswith("_enabled"):
                    call_kwargs[k] = True
                elif k in ["copts", "linkopts", "srcs", "hdrs", "deps", "data", "features"]:
                    call_kwargs[k] = []
        if "visibility" not in call_kwargs:
            call_kwargs["visibility"] = ["//visibility:public"]
        return implementation(name = name, **call_kwargs)
    return _wrapper

_macro = getattr(bazel_features.globals, "macro", None)
macro = _macro if _macro != None else _fallback_macro

jvm_provided_library = macro(
    attrs = {
        "lib": attr.label(mandatory = True, allow_files = False, providers = [JavaInfo, KotlinInfo]),
    },
    implementation = _jvm_provided_library,
)
