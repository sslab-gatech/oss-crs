#!/bin/bash
set -x

# Only run if current directory contains 'built-src'
if [[ "$PWD" == *"built-src"* ]]; then
  echo "Applying patch for built project..."
  
  # `/built-src/{proj-src}` to `/src/{proj-src}`
  export MOUNTED_WORKDIR=$(echo $PWD | sed 's/built-src/src/')
  export TEST_WORKDIR=$(echo $PWD | sed 's/built-src/test-src/')
  pushd $MOUNTED_WORKDIR

  # Now in /src/{proj-src}
  git config --global --add safe.directory $MOUNTED_WORKDIR
  git diff HEAD > /tmp/patch.diff

  popd
  # Now returned to `/built-src/{proj-src}`
  if [ -s /tmp/patch.diff ]; then
    echo "Applying patch..."
    git apply /tmp/patch.diff
    if [ -d $TEST_WORKDIR ]; then
      echo "Applying patch to test-src..."
      pushd $TEST_WORKDIR
      git apply /tmp/patch.diff
      popd
    else
      echo "No test-src directory found. Creating..."
      cp -r /src /test-src
    fi
  else
    if [ ! -d $TEST_WORKDIR ]; then
      echo "No test-src directory found. Creating..."
      cp -r /src /test-src || echo "Failed to copy /src to /test-src"
    fi
    echo "No patch file found at /tmp/patch.diff or it is empty. Skipping git apply."
  fi
  cd $TEST_WORKDIR
else
  echo "Current directory does not contain 'built-src' which means it is not a built project. Skipping patch application."
fi