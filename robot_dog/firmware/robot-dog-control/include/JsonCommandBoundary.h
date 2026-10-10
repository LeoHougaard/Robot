#pragma once

#include <stddef.h>

inline bool isJsonWhitespace(char c) {
  return c == ' ' || c == '\t' || c == '\r' || c == '\n';
}

// ArduinoJson deliberately accepts a complete object followed by more bytes.
// Our wire protocol requires exactly one object per line: a lost delimiter
// must never turn two commands into an accepted first command. This checks
// only the outer boundary; ArduinoJson still validates all JSON syntax/types.
inline bool hasSingleJsonCommandObject(const char *data, size_t size) {
  size_t i = 0;
  while (i < size && isJsonWhitespace(data[i])) ++i;
  if (i == size || data[i] != '{') return false;
  size_t depth = 0;
  bool quoted = false;
  bool escaped = false;
  bool complete = false;
  for (; i < size; ++i) {
    const char c = data[i];
    if (complete) {
      if (!isJsonWhitespace(c)) return false;
      continue;
    }
    if (quoted) {
      if (static_cast<unsigned char>(c) < 0x20) return false;
      if (escaped) escaped = false;
      else if (c == '\\') escaped = true;
      else if (c == '"') quoted = false;
      continue;
    }
    if (static_cast<unsigned char>(c) < 0x20 && !isJsonWhitespace(c)) return false;
    if (c == '"') quoted = true;
    else if (c == '{' || c == '[') ++depth;
    else if (c == '}' || c == ']') {
      if (depth == 0) return false;
      if (--depth == 0) {
        if (c != '}') return false;
        complete = true;
      }
    }
  }
  return complete;
}
