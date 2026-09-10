#!/bin/sh

# Dropbear forced command. Dropbear is compiled with the SFTP subsystem
# pointing at /usr/libexec/sftp-server, which does not exist on this firmware
# and cannot be created on a read-only rootfs. Rather than patch the path,
# intercept the request here and exec the bundled server instead. This is the
# same approach spruceOS uses on the A30.

BIN_DIR="$(dirname "$0")"

if uname -m 2>/dev/null | grep -q 64; then
	ARCH_DIR=arm64
else
	ARCH_DIR=arm
fi

cd /mnt/SDCARD 2>/dev/null || cd / || exit 1

case "$SSH_ORIGINAL_COMMAND" in
	*sftp-server*)
		if [ -x "$BIN_DIR/$ARCH_DIR/gesftpserver" ]; then
			exec "$BIN_DIR/$ARCH_DIR/gesftpserver"
		fi
		echo "DevBridge: gesftpserver is missing; SFTP is unavailable" >&2
		exit 1
		;;
esac

if [ -n "$SSH_ORIGINAL_COMMAND" ]; then
	exec /bin/sh -c "$SSH_ORIGINAL_COMMAND"
fi

exec /bin/sh
