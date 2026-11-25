cmake_minimum_required(VERSION 3.16)
project(pub2json CXX)

set(CMAKE_CXX_STANDARD 11)
set(CMAKE_CXX_STANDARD_REQUIRED ON)

option(PUB2JSON_STATIC "Link libmspub and its dependencies statically (for a self-contained binary)" OFF)

find_package(PkgConfig REQUIRED)
if(PUB2JSON_STATIC)
  set(PKG_CONFIG_EXECUTABLE_ARGS --static)
  pkg_check_modules(MSPUB REQUIRED IMPORTED_TARGET libmspub-0.1 librevenge-0.0 librevenge-stream-0.0)
  # pkg-config --static adds the private dependencies (icu, zlib, ...).
  execute_process(COMMAND ${PKG_CONFIG_EXECUTABLE} --static --libs libmspub-0.1 librevenge-stream-0.0 librevenge-0.0
                  OUTPUT_VARIABLE MSPUB_STATIC_LIBS OUTPUT_STRIP_TRAILING_WHITESPACE)
  separate_arguments(MSPUB_STATIC_LIBS)
else()
  pkg_check_modules(MSPUB REQUIRED IMPORTED_TARGET libmspub-0.1 librevenge-0.0 librevenge-stream-0.0)
endif()

add_executable(pub2json pub2json.cpp)

if(PUB2JSON_STATIC)
  target_include_directories(pub2json PRIVATE ${MSPUB_INCLUDE_DIRS})
  target_compile_definitions(pub2json PRIVATE LIBMSPUB_STATIC REVENGE_STATIC REVENGE_STREAM_STATIC)
  if(WIN32)
    target_link_options(pub2json PRIVATE -static -municode)
  endif()
  target_link_libraries(pub2json PRIVATE ${MSPUB_STATIC_LIBS})
else()
  if(WIN32)
    target_link_options(pub2json PRIVATE -municode)
  endif()
  target_link_libraries(pub2json PRIVATE PkgConfig::MSPUB)
endif()

install(TARGETS pub2json RUNTIME DESTINATION bin)
