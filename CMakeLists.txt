# SPDX-License-Identifier: Apache-2.0

zephyr_get(zkeyring_use_profile_key SYSBUILD LOCAL VAR ZKEYRING_USE_PROFILE_KEY)
if(zkeyring_use_profile_key AND NOT CONFIG_ZSIGNING)
  message(FATAL_ERROR "SB_CONFIG_ZKEYRING_PROFILE_PUBLIC_KEY requires CONFIG_ZSIGNING in the application")
endif()

if(CONFIG_ZSIGNING)
  set_target_properties(zephyr_property_target PROPERTIES
    SIGNING_SCRIPT "${CMAKE_CURRENT_LIST_DIR}/cmake/mcuboot.cmake"
  )
endif()
