/usr/bin/bazel --server_javabase=/usr/lib/jvm/java-25-openjdk-amd64/ build  //... @rules_jvm//... @lib//... --nouse_action_cache --lockfile_mode=off --experimental_cc_static_library
