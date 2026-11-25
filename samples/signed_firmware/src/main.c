/*
 * Copyright (c) 2026 Romain Pelletant
 *
 * SPDX-License-Identifier: Apache-2.0
 */

#include <stdio.h>

int main(void)
{
	printf("zkeyring demo firmware %s\n", CONFIG_MCUBOOT_IMGTOOL_SIGN_VERSION);

	return 0;
}
