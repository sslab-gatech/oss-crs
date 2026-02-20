#include "mock.h"

extern int LLVMFuzzerTestOneInput(const uint8_t *Data, size_t Size) {
  parse_buffer_section(Data, Size);
  return 0;
}
