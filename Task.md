# Your task

Write bazel cache auditor in Python.

## Cache location

vendor/bazel-external

Use --cache option to customize it

## Audit

For every file in cache check if it is a binary:
- Archive (tar.gz , tar.xz, zip etc)
  - Check if archive contains binary files (other archives, elf binaries, jar files)
    - Add ARCHIVE file to the report
  - Otherwise skip
- Binary file (elf binary, jar file)
  - Add to the report


## Report format

Binaries
--------
<relative path to the binary file>


