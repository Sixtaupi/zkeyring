# SPDX-License-Identifier: Apache-2.0

# SIGNING_SCRIPT for a build whose MCUboot signature is applied by zkeyring.
function(zkeyring_mcuboot_tasks)
  if(NOT CONFIG_BOOTLOADER_MCUBOOT)
    return()
  endif()

  if(NOT CONFIG_BUILD_OUTPUT_BIN OR NOT IMGTOOL)
    message(FATAL_ERROR "zkeyring requires a .bin build and the MCUboot imgtool")
  endif()

  if(CONFIG_MCUBOOT_ENCRYPTION_KEY_FILE OR CONFIG_MCUBOOT_EXTRA_IMGTOOL_ARGS
     OR CONFIG_MCUBOOT_GENERATE_CONFIRMED_IMAGE OR CONFIG_MCUBOOT_GENERATE_UNSIGNED_IMAGE
     OR CONFIG_MCUBOOT_BOOTLOADER_SIGNATURE_TYPE_PURE OR CONFIG_MCUBOOT_BOOTLOADER_USES_SHA512
     OR CONFIG_MCUBOOT_BOOTLOADER_MODE_DIRECT_XIP
     OR CONFIG_MCUBOOT_BOOTLOADER_MODE_DIRECT_XIP_WITH_REVERT
     OR CONFIG_MCUBOOT_BOOTLOADER_MODE_RAM_LOAD
     OR CONFIG_MCUBOOT_BOOTLOADER_MODE_RAM_LOAD_WITH_REVERT
     OR CONFIG_MCUBOOT_BOOTLOADER_MODE_SINGLE_APP_RAM_LOAD
     OR CONFIG_MCUBOOT_BOOTLOADER_MODE_FIRMWARE_UPDATER
     OR CONFIG_MCUBOOT_IMGTOOL_UUID_VID OR CONFIG_MCUBOOT_IMGTOOL_UUID_CID)
    message(FATAL_ERROR "This MCUboot configuration is outside the zkeyring .bin signing scope")
  endif()

  string(CONFIGURE "${CONFIG_MCUBOOT_SIGNATURE_KEY_FILE}" key_file)
  if(key_file STREQUAL "")
    message(FATAL_ERROR "Set the MCUboot signing key file to an ECDSA P-256 public PEM")
  endif()
  if(NOT IS_ABSOLUTE "${key_file}")
    if(EXISTS "${APPLICATION_CONFIG_DIR}/${key_file}")
      set(key_file "${APPLICATION_CONFIG_DIR}/${key_file}")
    else()
      set(key_file "${WEST_TOPDIR}/${key_file}")
    endif()
  endif()
  if(NOT EXISTS "${key_file}" OR IS_DIRECTORY "${key_file}")
    message(FATAL_ERROR "MCUboot signing public PEM not found at ${key_file}")
  endif()

  execute_process(
    COMMAND "${PYTHON_EXECUTABLE}" "${IMGTOOL}" keyinfo --require public -k "${key_file}"
    RESULT_VARIABLE result
    OUTPUT_QUIET
    ERROR_VARIABLE imgtool_error
    ERROR_STRIP_TRAILING_WHITESPACE
  )
  if(NOT result STREQUAL "0")
    message(FATAL_ERROR "zkeyring requires a public-only MCUboot PEM and an imgtool with keyinfo.\n"
                        "${IMGTOOL} keyinfo failed with status ${result}.\n${imgtool_error}")
  endif()

  execute_process(
    COMMAND "${PYTHON_EXECUTABLE}" "${IMGTOOL}" getpub -k "${key_file}" --encoding pem
    RESULT_VARIABLE result
    OUTPUT_VARIABLE public_key
    ERROR_VARIABLE imgtool_error
    ERROR_STRIP_TRAILING_WHITESPACE
  )
  if(NOT result STREQUAL "0")
    message(FATAL_ERROR "Could not extract the MCUboot signing public key.\n"
                        "${IMGTOOL} getpub failed with status ${result}.\n${imgtool_error}")
  endif()
  set_property(DIRECTORY APPEND PROPERTY CMAKE_CONFIGURE_DEPENDS "${key_file}")

  if(CONFIG_MCUBOOT_BOOTLOADER_MODE_SINGLE_APP)
    dt_nodelabel(slot NODELABEL "slot0_partition" REQUIRED)
  else()
    dt_nodelabel(slot NODELABEL "slot1_partition" REQUIRED)
  endif()
  dt_prop(slot_size PATH "${slot}" PROPERTY "reg" INDEX 1 REQUIRED)

  if(CONFIG_MCUBOOT_IMGTOOL_OVERWRITE_ONLY)
    set(align 1)
    set(overwrite_only true)
  else()
    dt_chosen(flash_node PROPERTY "zephyr,flash")
    dt_prop(align PATH "${flash_node}" PROPERTY "write-block-size")
    if(NOT align)
      set(align 4)
    endif()
    set(overwrite_only false)
  endif()

  if(NOT KERNEL_NAME MATCHES "^[a-zA-Z0-9_-]+$"
     OR NOT CONFIG_MCUBOOT_IMGTOOL_SIGN_VERSION MATCHES "^[0-9]+\\.[0-9]+\\.[0-9]+(\\+[0-9]+)?$")
    message(FATAL_ERROR "zkeyring requires a simple kernel name and a numeric MCUboot image version")
  endif()

  math(EXPR header_size "${CONFIG_ROM_START_OFFSET}")
  math(EXPR slot_size "${slot_size}")
  math(EXPR align "${align}")

  set(metadata "${APPLICATION_BINARY_DIR}/zkeyring-sign.json")
  set(public_pem "${APPLICATION_BINARY_DIR}/zkeyring-public.pem")
  configure_file(
    "${CMAKE_CURRENT_FUNCTION_LIST_DIR}/zkeyring-sign.json.in"
    "${metadata}"
    @ONLY
  )
  file(GENERATE OUTPUT "${public_pem}" CONTENT "${public_key}")

  if(CONFIG_ZSIGNING_DEFERRED)
    return()
  endif()

  zephyr_get(config_file SYSBUILD LOCAL VAR ZKEYRING_CONFIG)
  zephyr_get(profile SYSBUILD LOCAL VAR ZKEYRING_PROFILE)
  if("${config_file}" STREQUAL "")
    set(config_file "${CONFIG_ZSIGNING_PROFILE_FILE}")
  endif()
  cmake_path(ABSOLUTE_PATH config_file BASE_DIRECTORY "${APPLICATION_SOURCE_DIR}" NORMALIZE)
  if(NOT EXISTS "${config_file}" OR IS_DIRECTORY "${config_file}")
    message(FATAL_ERROR "zkeyring profile file not found: ${config_file}. "
                        "Set CONFIG_ZSIGNING_PROFILE_FILE or ZKEYRING_CONFIG.")
  endif()
  if("${profile}" STREQUAL "")
    set(profile "${CONFIG_ZSIGNING_PROFILE}")
  endif()

  # Run the sources of this module, even when zkeyring is also installed.
  cmake_path(SET zkeyring_python_dir NORMALIZE
             "${CMAKE_CURRENT_FUNCTION_LIST_DIR}/../scripts/signing")
  set(signed_bin "${ZEPHYR_BINARY_DIR}/${KERNEL_NAME}.signed.bin")
  add_custom_command(
    OUTPUT "${signed_bin}"
    COMMAND "${CMAKE_COMMAND}" -E env
            "--modify" "PYTHONPATH=path_list_prepend:${zkeyring_python_dir}"
            "${PYTHON_EXECUTABLE}" -m zkeyring.cli sign
            --build-dir "${APPLICATION_BINARY_DIR}"
            --imgtool "${IMGTOOL}"
            --config "${config_file}"
            --profile "${profile}"
            --output "${signed_bin}"
    DEPENDS
      "${logical_target_for_zephyr_elf}"
      "${ZEPHYR_BINARY_DIR}/${KERNEL_BIN_NAME}"
      "${IMGTOOL}"
      "${config_file}"
      "${metadata}"
      "${public_pem}"
    COMMENT "Signing ${KERNEL_BIN_NAME} with zkeyring"
    VERBATIM
  )
  add_custom_target(zkeyring_sign ALL DEPENDS "${signed_bin}")

  set_property(TARGET runners_yaml_props_target PROPERTY bin_file "${KERNEL_NAME}.signed.bin")
  set(BYPRODUCT_KERNEL_SIGNED_BIN_NAME "${signed_bin}" CACHE FILEPATH "Signed kernel bin file" FORCE)
endfunction()

zkeyring_mcuboot_tasks()
