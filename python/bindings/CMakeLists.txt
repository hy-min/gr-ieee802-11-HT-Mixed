# SPDX-License-Identifier: GPL-3.0-or-later

########################################################################
# Check if there is C++ code at all
########################################################################
if(NOT ieee802_11_sources)
    message(STATUS "No C++ sources... skipping python bindings")
    return()
endif()

include(GrPybind)

########################################################################
# NumPy include (needed by python_bindings.cc)
########################################################################
execute_process(
    COMMAND ${PYTHON_EXECUTABLE} -c "import numpy as np; print(np.get_include())"
    OUTPUT_VARIABLE NUMPY_INCLUDE_DIR
    OUTPUT_STRIP_TRAILING_WHITESPACE
)

if(NOT EXISTS "${NUMPY_INCLUDE_DIR}/numpy/arrayobject.h")
    message(FATAL_ERROR "NumPy headers not found at: ${NUMPY_INCLUDE_DIR}")
endif()

########################################################################
# Python Bindings sources
########################################################################
set(ieee802_11_python_files
    chunks_to_symbols_python.cc
    constellations_python.cc
    decode_mac_python.cc
    ether_encap_python.cc
    extract_csi_python.cc
    frame_equalizer_python.cc
    ht_symbol_splitter_python.cc
    mac_python.cc
    mapper_python.cc
    parse_mac_python.cc
    signal_field_python.cc
    ht_header_tagged_python.cc
    sync_long_python.cc
    sync_short_python.cc
    sync_short_fused_python.cc
    equalizer_python.cc
    insert_ht_training_python.cc
    python_bindings.cc
)

########################################################################
# Build the pybind11 module
########################################################################
GR_PYBIND_MAKE_OOT(
    ieee802_11
    ../..
    gr::ieee802_11
    "${ieee802_11_python_files}"
)

########################################################################
# Include dirs (NumPy + generated docstrings)
########################################################################
target_include_directories(ieee802_11_python PRIVATE
    ${NUMPY_INCLUDE_DIR}
    ${CMAKE_CURRENT_BINARY_DIR}
    ${CMAKE_CURRENT_BINARY_DIR}/docstrings
)

########################################################################
# Ensure docstrings generated before compiling bindings
########################################################################
if(TARGET ieee802_11_docstrings)
    add_dependencies(ieee802_11_python ieee802_11_docstrings)
endif()

########################################################################
# Install
########################################################################
install(TARGETS ieee802_11_python
    DESTINATION ${GR_PYTHON_DIR}/ieee802_11
    COMPONENT pythonapi
)

