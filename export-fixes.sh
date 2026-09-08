bazel vendor $(bazel query "//... except (kind('.*_test', //...) union attr(testonly, 1, //...))") --vendor_dir vendor

