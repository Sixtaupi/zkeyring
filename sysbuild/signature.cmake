# SPDX-License-Identifier: Apache-2.0

# Apply the zkeyring choice before Zephyr writes each image's configuration.
if(SB_CONFIG_ZKEYRING_SIGNATURE_RSA_2048 OR SB_CONFIG_ZKEYRING_SIGNATURE_RSA_3072)
  set(SB_CONFIG_SIGNATURE_TYPE RSA)
  if(ZCMAKE_APPLICATION STREQUAL "mcuboot")
    if(SB_CONFIG_ZKEYRING_SIGNATURE_RSA_3072)
      set_config_int(mcuboot CONFIG_BOOT_SIGNATURE_TYPE_RSA_LEN 3072)
    else()
      set_config_int(mcuboot CONFIG_BOOT_SIGNATURE_TYPE_RSA_LEN 2048)
    endif()
  endif()
elseif(SB_CONFIG_ZKEYRING_SIGNATURE_ED25519)
  set(SB_CONFIG_SIGNATURE_TYPE ED25519)
else()
  set(SB_CONFIG_SIGNATURE_TYPE ECDSA_P256)
endif()

foreach(key_type NONE RSA ECDSA_P256 ED25519)
  set(SB_CONFIG_BOOT_SIGNATURE_TYPE_${key_type} n)
endforeach()
set(SB_CONFIG_BOOT_SIGNATURE_TYPE_${SB_CONFIG_SIGNATURE_TYPE} y)
