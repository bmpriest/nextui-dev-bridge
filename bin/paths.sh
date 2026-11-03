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
