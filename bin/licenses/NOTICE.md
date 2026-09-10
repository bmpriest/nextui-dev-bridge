# Bundled third-party binaries

DevBridge redistributes prebuilt binaries it did not compile. Each is recorded
here with its origin and license so the provenance is traceable.

## `arm/dropbearmulti`

Dropbear SSH 2025.88, armv7 static-pie. Taken from
`NextUI/workspace/my282/package/ssh/dropbearmulti`, which in turn took it from
`spruceOS/spruce/bin/SSH/bin/dropbearmulti`. It is the A30-tested build.

Dropbear is distributed under an MIT-style license.
Upstream: https://github.com/mkj/dropbear

## `arm/gesftpserver`

Green End SFTP Server, armv7, dynamically linked. Taken from
`spruceOS/spruce/bin/gesftpserver`. It provides the SFTP subsystem that
Dropbear expects to find at `/usr/libexec/sftp-server`.

Green End SFTP Server is by Richard Kettlewell and is licensed under the
GNU GPL, version 2 or later.
Upstream: https://www.greenend.org.uk/rjk/sftpserver/

Note: spruceOS ships this binary without a matching license file — its
`Licenses/tools/OpenSSH.txt` covers different tools (scp, sftp, ssh-keygen,
ssh-keyscan) and does **not** apply to gesftpserver. The license stated above
is from the upstream project and has not been verified against this specific
build. **Obtain the full GPL text and confirm the version before
redistributing this pak publicly**; GPL redistribution also carries a
corresponding-source obligation that a binary-only copy does not satisfy.

## `my282/minui-list`, `my282/jq`

The same builds packaged with the Battery Diagnostic pak. Licenses are in
`LICENSE.minui-list` and `LICENSE.jq`.
