# Copyright (c) 2021 Nordic Semiconductor ASA
# SPDX-License-Identifier: Apache-2.0
#
# This CMake file is picked by the Zephyr build system because it is defined
# as the module CMake entry point (see zephyr/module.yml).


# Register headers when the optional application includes are present.
if(IS_DIRECTORY "${CMAKE_CURRENT_SOURCE_DIR}/include")
  zephyr_syscall_include_directories(include)
  zephyr_include_directories(include)
endif()

if(CONFIG_ZSIGNING)
  set_target_properties(zephyr_property_target PROPERTIES
    SIGNING_SCRIPT "${CMAKE_CURRENT_LIST_DIR}/cmake/mcuboot.cmake"
  )
endif()
