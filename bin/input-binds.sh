# Sourced by game-launch: undo DraStic's input binds completely.
#
# game-launch binds the virtual pad over the physical pad's /dev/input nodes
# while DraStic runs. A plain umount fails while anything still holds the node
# open (NDS.pak's button handler can outlive DraStic by a moment), and that
# failure was silent: each such launch left one more bind behind. The next
# launch's mirror then opened /dev/input/event3 expecting the real pad, got
# the virtual one, and copied it into itself - a flood of D-pad events that
# scrolled DraStic's menus on their own (seven stacked binds on a Brick,
# 2026-10-26).

# input_unbind PATH: remove every bind stacked on PATH; lazily if busy.
input_unbind() {
	_node=$1
	_n=0
	while grep -q " $_node " "${PROC_MOUNTS:-/proc/mounts}" 2>/dev/null; do
		umount "$_node" 2>/dev/null || umount -l "$_node" 2>/dev/null || return 1
		_n=$((_n + 1))
		[ "$_n" -lt 16 ] || return 1
	done
	return 0
}
