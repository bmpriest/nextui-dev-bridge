.PHONY: dev test clean

ARCHIVE := dist/DevBridge.pak.zip

dev:
	mkdir -p dist
	rm -f "$(ARCHIVE)"
	cd .. && zip -q -r "DevBridge.pak/$(ARCHIVE)" "DevBridge.pak" \
		-x "DevBridge.pak/bin/tests/*" \
		-x "DevBridge.pak/dist/*" \
		-x "DevBridge.pak/devbridge-sessions/*" \
		-x "*/__pycache__/*" \
		-x "*.pyc" \
		-x "DevBridge.pak/Makefile"

test:
	bin/tests/test-devbridge.sh

clean:
	rm -f "$(ARCHIVE)"

# Cross-compiles udpsend for both targets using the toolchain images that
# NextUI already builds. BusyBox nc on these devices has no UDP support.
binaries:
	docker run --rm -v "$(PWD):/work" -w /work -u "$(shell id -u):$(shell id -g)" \
		nextui-my282-toolchain:local \
		sh -c '$${CROSS_COMPILE}gcc -O2 -s -o bin/arm/udpsend src/udpsend.c && \
			$${CROSS_COMPILE}gcc -O2 -Wall -Wextra -s -o bin/arm/devbridge-input src/devbridge-input.c'
	docker run --rm -v "$(PWD):/work" -w /work -u "$(shell id -u):$(shell id -g)" \
		ghcr.io/loveretro/tg5040-toolchain:latest \
		sh -c '$${CROSS_COMPILE}gcc -O2 -s -o bin/arm64/udpsend src/udpsend.c && \
			$${CROSS_COMPILE}gcc -O2 -Wall -Wextra -s -o bin/arm64/devbridge-input src/devbridge-input.c'
