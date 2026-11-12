#!/bin/sh

# Resolve the firmware's application and state roots once for every entry point.
: "${SDCARD_PATH:=/mnt/SDCARD}"
case "$(basename "$(dirname "$PAK_DIR")")" in
	App)
		[ "${PLATFORM:-}" = spruce ] || SPRUCE_PLATFORM=${PLATFORM:-}
		PLATFORM=spruce
		;;
	*) : "${PLATFORM:=$(basename "$(dirname "$PAK_DIR")")}" ;;
esac
if [ "$PLATFORM" = spruce ]; then
	: "${USERDATA_PATH:=$SDCARD_PATH/Saves/spruce}"
	: "${LOGS_PATH:=$USERDATA_PATH}"
	: "${SPRUCE_APP_ROOT:=$SDCARD_PATH/App}"
	: "${SPRUCE_EMU_ROOT:=$SDCARD_PATH/Emu}"
else
	: "${USERDATA_PATH:=$SDCARD_PATH/.userdata/$PLATFORM}"
	: "${LOGS_PATH:=$USERDATA_PATH/logs}"
fi
export PLATFORM SPRUCE_PLATFORM USERDATA_PATH LOGS_PATH

# Spruce devices whose menu and RetroArch read only the built-in pad: presses
# go straight into its evdev node (bin/spruce-input) instead of a uinput pad.
# Named pads: ANBERNIC-keys (H700), TRIMUI Player1 (Brick), MIYOO Pad1 (A30).
# The Miyoo Mini family (SigmaStar SSD202: Mini Plus, Mini Flip) has none of
# these, only its keyboard on event0, and its kernel may lack uinput.
spruce_direct_input() {
	[ "$PLATFORM" = spruce ] || return 1
	[ -x "$SDCARD_PATH/spruce/bin64/sendevent" ] || [ -x "$SDCARD_PATH/spruce/bin/sendevent" ] || return 1
	grep -qxE 'ANBERNIC-keys|TRIMUI Player1|MIYOO Pad1' /sys/class/input/event*/device/name 2>/dev/null && return 0
	grep -q SStar /proc/cpuinfo 2>/dev/null && [ -e /dev/input/event0 ]
}
