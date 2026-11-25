// pub2json - dump a Microsoft Publisher (.pub) file as JSON Lines.
//
// libmspub parses the .pub file and reports its content through the
// librevenge drawing interface (startPage, drawPath, openParagraph, ...).
// This program writes every such call as one JSON object per line:
//
//   {"op":"drawPath","p":{"svg:d":[{"librevenge:path-action":"M","svg:x":{"v":2.28,"u":"in"}, ...}], ...}}
//
// Properties keep their librevenge names. Numbers are {"v":value,"u":unit}
// with unit one of "in", "pt", "twip", "%", "" (generic). Strings stay strings;
// binary data (images) is base64. pub2idml.py turns this stream into IDML.
//
// SPDX-License-Identifier: MIT

#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <string>
#include <vector>

#include <librevenge-stream/librevenge-stream.h>
#include <librevenge/librevenge.h>
#include <libmspub/libmspub.h>

#ifdef _WIN32
#include <fcntl.h>
#include <io.h>
#include <windows.h>
#endif

namespace
{

void putEscaped(std::string &out, const char *s)
{
  out += '"';
  for (const unsigned char *p = reinterpret_cast<const unsigned char *>(s); p && *p; ++p)
  {
    switch (*p)
    {
    case '"': out += "\\\""; break;
    case '\\': out += "\\\\"; break;
    case '\n': out += "\\n"; break;
    case '\r': out += "\\r"; break;
    case '\t': out += "\\t"; break;
    default:
      if (*p < 0x20)
      {
        char buf[8];
        std::snprintf(buf, sizeof buf, "\\u%04x", *p);
        out += buf;
      }
      else
        out += static_cast<char>(*p);
    }
  }
  out += '"';
}

const char *unitName(librevenge::RVNGUnit u)
{
  switch (u)
  {
  case librevenge::RVNG_INCH: return "in";
  case librevenge::RVNG_POINT: return "pt";
  case librevenge::RVNG_TWIP: return "twip";
  case librevenge::RVNG_PERCENT: return "%";
  case librevenge::RVNG_GENERIC: return "";
  default: return nullptr;
  }
}

void putPropList(std::string &out, const librevenge::RVNGPropertyList &pl);

void putVector(std::string &out, const librevenge::RVNGPropertyListVector &vec)
{
  out += '[';
  for (unsigned long i = 0; i < vec.count(); ++i)
  {
    if (i)
      out += ',';
    putPropList(out, vec[i]);
  }
  out += ']';
}

void putProperty(std::string &out, const librevenge::RVNGProperty &prop)
{
  const char *unit = unitName(prop.getUnit());
  librevenge::RVNGString str = prop.getStr();
  if (unit)
  {
    char buf[64];
    double v = prop.getDouble();
    if (prop.getUnit() == librevenge::RVNG_PERCENT)
      v *= 100.0; // librevenge stores 50% as 0.5
    std::snprintf(buf, sizeof buf, "%.6g", v);
    out += "{\"v\":";
    out += buf;
    out += ",\"u\":\"";
    out += unit;
    out += "\",\"s\":";
    putEscaped(out, str.cstr());
    out += '}';
  }
  else
    putEscaped(out, str.cstr());
}

void putPropList(std::string &out, const librevenge::RVNGPropertyList &pl)
{
  out += '{';
  bool first = true;
  librevenge::RVNGPropertyList::Iter it(pl);
  for (it.rewind(); it.next();)
  {
    if (!first)
      out += ',';
    first = false;
    putEscaped(out, it.key());
    out += ':';
    if (it.child())
      putVector(out, *it.child());
    else if (it())
      putProperty(out, *it());
    else
      out += "null";
  }
  out += '}';
}

class JsonPainter : public librevenge::RVNGDrawingInterface
{
public:
  explicit JsonPainter(FILE *out) : m_out(out) {}

private:
  FILE *m_out;

  void emit(const char *op, const librevenge::RVNGPropertyList *pl = nullptr, const char *text = nullptr)
  {
    std::string line = "{\"op\":\"";
    line += op;
    line += '"';
    if (pl)
    {
      line += ",\"p\":";
      putPropList(line, *pl);
    }
    if (text)
    {
      line += ",\"t\":";
      putEscaped(line, text);
    }
    line += "}\n";
    std::fwrite(line.data(), 1, line.size(), m_out);
  }

#define OP_PL(name) \
  void name(const librevenge::RVNGPropertyList &pl) override { emit(#name, &pl); }
#define OP_0(name) \
  void name() override { emit(#name); }

  OP_PL(startDocument)
  OP_0(endDocument)
  OP_PL(setDocumentMetaData)
  OP_PL(defineEmbeddedFont)
  OP_PL(startPage)
  OP_0(endPage)
  OP_PL(startMasterPage)
  OP_0(endMasterPage)
  OP_PL(setStyle)
  OP_PL(startLayer)
  OP_0(endLayer)
  OP_PL(startEmbeddedGraphics)
  OP_0(endEmbeddedGraphics)
  OP_PL(openGroup)
  OP_0(closeGroup)
  OP_PL(drawRectangle)
  OP_PL(drawEllipse)
  OP_PL(drawPolygon)
  OP_PL(drawPolyline)
  OP_PL(drawPath)
  OP_PL(drawGraphicObject)
  OP_PL(drawConnector)
  OP_PL(startTextObject)
  OP_0(endTextObject)
  OP_PL(startTableObject)
  OP_PL(openTableRow)
  OP_0(closeTableRow)
  OP_PL(openTableCell)
  OP_0(closeTableCell)
  OP_PL(insertCoveredTableCell)
  OP_0(endTableObject)
  OP_0(insertTab)
  OP_0(insertSpace)
  OP_0(insertLineBreak)
  OP_PL(insertField)
  OP_PL(openOrderedListLevel)
  OP_PL(openUnorderedListLevel)
  OP_0(closeOrderedListLevel)
  OP_0(closeUnorderedListLevel)
  OP_PL(openListElement)
  OP_0(closeListElement)
  OP_PL(defineParagraphStyle)
  OP_PL(openParagraph)
  OP_0(closeParagraph)
  OP_PL(defineCharacterStyle)
  OP_PL(openSpan)
  OP_0(closeSpan)
  OP_PL(openLink)
  OP_0(closeLink)

#undef OP_PL
#undef OP_0

  void insertText(const librevenge::RVNGString &text) override { emit("insertText", nullptr, text.cstr()); }
};

bool readFile(const char *path, std::vector<unsigned char> &data)
{
#ifdef _WIN32
  // Accept UTF-8 paths on Windows too (argv is converted in main).
  int n = MultiByteToWideChar(CP_UTF8, 0, path, -1, nullptr, 0);
  std::wstring w(static_cast<size_t>(n), L'\0');
  MultiByteToWideChar(CP_UTF8, 0, path, -1, &w[0], n);
  FILE *f = _wfopen(w.c_str(), L"rb");
#else
  FILE *f = std::fopen(path, "rb");
#endif
  if (!f)
    return false;
  unsigned char buf[65536];
  size_t got;
  while ((got = std::fread(buf, 1, sizeof buf, f)) > 0)
    data.insert(data.end(), buf, buf + got);
  std::fclose(f);
  return true;
}

int run(int argc, char **argv)
{
  if (argc < 2 || !std::strcmp(argv[1], "-h") || !std::strcmp(argv[1], "--help"))
  {
    std::fprintf(stderr, "usage: pub2json FILE.pub [OUT.jsonl]\n"
                         "Writes the drawing calls libmspub reports as JSON Lines (default: stdout).\n");
    return argc < 2 ? 2 : 0;
  }
  if (!std::strcmp(argv[1], "--version"))
  {
    std::printf("pub2json 0.3.0 (libmspub)\n");
    return 0;
  }
  std::vector<unsigned char> data;
  if (!readFile(argv[1], data) || data.empty())
  {
    std::fprintf(stderr, "pub2json: cannot read %s\n", argv[1]);
    return 1;
  }
  librevenge::RVNGStringStream input(data.data(), static_cast<unsigned int>(data.size()));
  if (!libmspub::MSPUBDocument::isSupported(&input))
  {
    std::fprintf(stderr, "pub2json: %s is not a supported Publisher file\n", argv[1]);
    return 3;
  }
  FILE *out = stdout;
  if (argc > 2)
  {
#ifdef _WIN32
    int n = MultiByteToWideChar(CP_UTF8, 0, argv[2], -1, nullptr, 0);
    std::wstring w(static_cast<size_t>(n), L'\0');
    MultiByteToWideChar(CP_UTF8, 0, argv[2], -1, &w[0], n);
    out = _wfopen(w.c_str(), L"wb");
#else
    out = std::fopen(argv[2], "wb");
#endif
    if (!out)
    {
      std::fprintf(stderr, "pub2json: cannot write %s\n", argv[2]);
      return 1;
    }
  }
  JsonPainter painter(out);
  bool ok = libmspub::MSPUBDocument::parse(&input, &painter);
  if (out != stdout)
    std::fclose(out);
  else
    std::fflush(stdout);
  if (!ok)
  {
    std::fprintf(stderr, "pub2json: libmspub could not parse %s\n", argv[1]);
    return 4;
  }
  return 0;
}

} // namespace

#ifdef _WIN32
int wmain(int argc, wchar_t **wargv)
{
  _setmode(_fileno(stdout), _O_BINARY);
  std::vector<std::string> args;
  std::vector<char *> argv;
  for (int i = 0; i < argc; ++i)
  {
    int n = WideCharToMultiByte(CP_UTF8, 0, wargv[i], -1, nullptr, 0, nullptr, nullptr);
    std::string s(static_cast<size_t>(n), '\0');
    WideCharToMultiByte(CP_UTF8, 0, wargv[i], -1, &s[0], n, nullptr, nullptr);
    s.resize(std::strlen(s.c_str()));
    args.push_back(s);
  }
  for (auto &s : args)
    argv.push_back(&s[0]);
  return run(argc, argv.data());
}
#else
int main(int argc, char **argv)
{
  return run(argc, argv);
}
#endif
