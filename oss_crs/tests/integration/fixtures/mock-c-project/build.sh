#!/bin/bash -eu

$CC $CFLAGS -c $SRC/mock-c/mock.c -o $SRC/mock-c/mock.o
for fuzzer in $(find $SRC/fuzz -name 'fuzz*.c'); do
    fname=$(basename $fuzzer)
    fuzzer_name=${fname%.*}
    fuzzer_obj=${fuzzer%.*}.o
    $CC $CFLAGS -I$SRC/mock-c/ -c $fuzzer -o $fuzzer_obj
    $CXX $CXXFLAGS $LIB_FUZZING_ENGINE $fuzzer_obj $SRC/mock-c/mock.o -o $OUT/$fuzzer_name
done

cp -r $SRC/mock-c $OUT/src
