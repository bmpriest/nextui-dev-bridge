#include <errno.h>
#include <fcntl.h>
#include <linux/input.h>
#include <linux/uinput.h>
#include <signal.h>
#include <stdbool.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <strings.h>
#include <sys/socket.h>
#include <sys/stat.h>
#include <sys/un.h>
#include <unistd.h>

#define DEFAULT_SOCKET "/tmp/DevBridge-input.sock"
#define DEFAULT_TAP_MS 80

/* Linux 4.9-era device headers predate the geographic gamepad aliases. */
#ifndef BTN_SOUTH
#define BTN_SOUTH 0x130
#define BTN_EAST  0x131
#define BTN_NORTH 0x133
#define BTN_WEST  0x134
#endif

static volatile sig_atomic_t running = 1;
static const char *socket_path = DEFAULT_SOCKET;
static int uinput_fd = -1;
static int server_fd = -1;

struct button {
	const char *name;
	unsigned short type;
	unsigned short code;
	int value;
};

/*
 * Match TRIMUI Player1's SDL layout.  In particular, BTN_SOUTH/EAST and
 * BTN_NORTH/WEST become SDL buttons 0/1 and 2/3 respectively; NextUI's
 * tg5040 platform maps those indices to B/A and Y/X.
 */
static struct button buttons[] = {
	{"b",      EV_KEY, BTN_SOUTH, 0},
	{"a",      EV_KEY, BTN_EAST, 0},
	{"y",      EV_KEY, BTN_NORTH, 0},
	{"x",      EV_KEY, BTN_WEST, 0},
	{"l1",     EV_KEY, BTN_TL, 0},
	{"r1",     EV_KEY, BTN_TR, 0},
	{"select", EV_KEY, BTN_SELECT, 0},
	{"start",  EV_KEY, BTN_START, 0},
	{"menu",   EV_KEY, BTN_MODE, 0},
	{"l3",     EV_KEY, BTN_THUMBL, 0},
	{"r3",     EV_KEY, BTN_THUMBR, 0},
	{"up",     EV_ABS, ABS_HAT0Y, 0},
	{"down",   EV_ABS, ABS_HAT0Y, 0},
	{"left",   EV_ABS, ABS_HAT0X, 0},
	{"right",  EV_ABS, ABS_HAT0X, 0},
	{"l2",     EV_ABS, ABS_Z, 0},
	{"r2",     EV_ABS, ABS_RZ, 0},
};

static void stop_running(int signal_number)
{
	(void)signal_number;
	running = 0;
	if (server_fd >= 0) {
		close(server_fd);
		server_fd = -1;
	}
}

static int emit(unsigned short type, unsigned short code, int value)
{
	struct input_event event;
	memset(&event, 0, sizeof(event));
	event.type = type;
	event.code = code;
	event.value = value;
	if (write(uinput_fd, &event, sizeof(event)) != sizeof(event))
		return -1;
	return 0;
}

static int sync_events(void)
{
	return emit(EV_SYN, SYN_REPORT, 0);
}

static struct button *find_button(const char *name)
{
	size_t i;
	for (i = 0; i < sizeof(buttons) / sizeof(buttons[0]); ++i) {
		if (strcasecmp(buttons[i].name, name) == 0)
			return &buttons[i];
	}
	return NULL;
}

static int axis_value(unsigned short code)
{
	if (code == ABS_HAT0X) {
		int left = find_button("left")->value;
		int right = find_button("right")->value;
		return right - left;
	}
	if (code == ABS_HAT0Y) {
		int up = find_button("up")->value;
		int down = find_button("down")->value;
		return down - up;
	}
	return 0;
}

static int set_button(struct button *button, bool pressed)
{
	int output;
	button->value = pressed ? 1 : 0;
	if (button->type == EV_KEY)
		output = button->value;
	else if (button->code == ABS_Z || button->code == ABS_RZ)
		output = pressed ? 32767 : 0;
	else
		output = axis_value(button->code);
	return emit(button->type, button->code, output) || sync_events();
}

static int release_all(void)
{
	size_t i;
	int failed = 0;
	for (i = 0; i < sizeof(buttons) / sizeof(buttons[0]); ++i) {
		if (!buttons[i].value)
			continue;
		buttons[i].value = 0;
		if (buttons[i].type == EV_KEY && emit(EV_KEY, buttons[i].code, 0))
			failed = 1;
	}
	if (emit(EV_ABS, ABS_HAT0X, 0) || emit(EV_ABS, ABS_HAT0Y, 0) ||
	    emit(EV_ABS, ABS_Z, 0) || emit(EV_ABS, ABS_RZ, 0) || sync_events())
		failed = 1;
	return failed ? -1 : 0;
}

static int create_virtual_controller(void)
{
	struct uinput_user_dev device;
	int keys[] = {BTN_SOUTH, BTN_EAST, BTN_NORTH, BTN_WEST, BTN_TL, BTN_TR,
		BTN_SELECT, BTN_START, BTN_MODE, BTN_THUMBL, BTN_THUMBR};
	int axes[] = {ABS_X, ABS_Y, ABS_Z, ABS_RX, ABS_RY, ABS_RZ,
		ABS_HAT0X, ABS_HAT0Y};
	size_t i;

	uinput_fd = open("/dev/uinput", O_WRONLY | O_NONBLOCK);
	if (uinput_fd < 0)
		return -1;
	if (ioctl(uinput_fd, UI_SET_EVBIT, EV_KEY) < 0 ||
	    ioctl(uinput_fd, UI_SET_EVBIT, EV_ABS) < 0)
		return -1;
	for (i = 0; i < sizeof(keys) / sizeof(keys[0]); ++i)
		if (ioctl(uinput_fd, UI_SET_KEYBIT, keys[i]) < 0)
			return -1;
	for (i = 0; i < sizeof(axes) / sizeof(axes[0]); ++i)
		if (ioctl(uinput_fd, UI_SET_ABSBIT, axes[i]) < 0)
			return -1;

	memset(&device, 0, sizeof(device));
	strncpy(device.name, "DevBridge Virtual Controller", UINPUT_MAX_NAME_SIZE - 1);
	device.id.bustype = BUS_USB;
	device.id.vendor = 0x045e;
	device.id.product = 0x028e;
	device.id.version = 0x0114;
	device.absmin[ABS_HAT0X] = -1;
	device.absmax[ABS_HAT0X] = 1;
	device.absmin[ABS_HAT0Y] = -1;
	device.absmax[ABS_HAT0Y] = 1;
	device.absmin[ABS_X] = -32768;
	device.absmax[ABS_X] = 32767;
	device.absmin[ABS_Y] = -32768;
	device.absmax[ABS_Y] = 32767;
	device.absmin[ABS_RX] = -32768;
	device.absmax[ABS_RX] = 32767;
	device.absmin[ABS_RY] = -32768;
	device.absmax[ABS_RY] = 32767;
	device.absmin[ABS_Z] = 0;
	device.absmax[ABS_Z] = 32767;
	device.absmin[ABS_RZ] = 0;
	device.absmax[ABS_RZ] = 32767;
	if (write(uinput_fd, &device, sizeof(device)) != sizeof(device) ||
	    ioctl(uinput_fd, UI_DEV_CREATE) < 0)
		return -1;
	return 0;
}

static void reply_to(int fd, const char *message)
{
	ssize_t ignored = write(fd, message, strlen(message));
	(void)ignored;
}

static void handle_command(int client, char *request)
{
	char *command;
	char *name;
	char *duration_text;
	struct button *button;
	long duration = DEFAULT_TAP_MS;

	command = strtok(request, " \t\r\n");
	name = strtok(NULL, " \t\r\n");
	duration_text = strtok(NULL, " \t\r\n");
	if (!command) {
		reply_to(client, "error empty command\n");
		return;
	}
	if (strcasecmp(command, "status") == 0) {
		reply_to(client, "ok ready\n");
		return;
	}
	if (strcasecmp(command, "release-all") == 0) {
		reply_to(client, release_all() ? "error write failed\n" : "ok\n");
		return;
	}
	button = name ? find_button(name) : NULL;
	if (!button) {
		reply_to(client, "error unknown button\n");
		return;
	}
	if (duration_text) {
		char *end = NULL;
		duration = strtol(duration_text, &end, 10);
		if (!end || *end || duration < 1 || duration > 10000) {
			reply_to(client, "error duration must be 1..10000 ms\n");
			return;
		}
	}
	if (strcasecmp(command, "press") == 0) {
		reply_to(client, set_button(button, true) ? "error write failed\n" : "ok\n");
	} else if (strcasecmp(command, "release") == 0) {
		reply_to(client, set_button(button, false) ? "error write failed\n" : "ok\n");
	} else if (strcasecmp(command, "tap") == 0) {
		if (set_button(button, true)) {
			reply_to(client, "error write failed\n");
			return;
		}
		usleep((useconds_t)duration * 1000);
		reply_to(client, set_button(button, false) ? "error write failed\n" : "ok\n");
	} else {
		reply_to(client, "error expected tap, press, release, release-all, or status\n");
	}
}

static int run_daemon(const char *path)
{
	struct sockaddr_un address;
	struct stat bound_socket;
	bool owns_socket = false;

	socket_path = path;
	if (create_virtual_controller() < 0) {
		perror("DevBridge input: /dev/uinput");
		return 1;
	}
	server_fd = socket(AF_UNIX, SOCK_STREAM, 0);
	if (server_fd < 0) {
		perror("DevBridge input: socket");
		return 1;
	}
	memset(&address, 0, sizeof(address));
	address.sun_family = AF_UNIX;
	if (strlen(path) >= sizeof(address.sun_path)) {
		fprintf(stderr, "DevBridge input: socket path is too long\n");
		return 1;
	}
	strncpy(address.sun_path, path, sizeof(address.sun_path) - 1);
	unlink(path);
	if (bind(server_fd, (struct sockaddr *)&address, sizeof(address)) < 0 ||
	    chmod(path, 0600) < 0 || listen(server_fd, 4) < 0) {
		perror("DevBridge input: listen");
		return 1;
	}
	if (lstat(path, &bound_socket) == 0)
		owns_socket = true;
	signal(SIGINT, stop_running);
	signal(SIGTERM, stop_running);
	while (running) {
		char request[256];
		int client = accept(server_fd, NULL, NULL);
		ssize_t count;
		if (client < 0) {
			if (errno == EINTR)
				continue;
			break;
		}
		count = read(client, request, sizeof(request) - 1);
		if (count > 0) {
			request[count] = '\0';
			handle_command(client, request);
		}
		close(client);
	}
	release_all();
	if (server_fd >= 0)
		close(server_fd);
	if (owns_socket) {
		struct stat current_socket;
		if (lstat(path, &current_socket) == 0 &&
		    current_socket.st_dev == bound_socket.st_dev &&
		    current_socket.st_ino == bound_socket.st_ino)
			unlink(path);
	}
	ioctl(uinput_fd, UI_DEV_DESTROY);
	close(uinput_fd);
	return 0;
}

static int send_command(int argc, char **argv)
{
	struct sockaddr_un address;
	char request[256];
	char response[256];
	size_t used = 0;
	int fd;
	int i;
	ssize_t count;

	fd = socket(AF_UNIX, SOCK_STREAM, 0);
	if (fd < 0)
		return 1;
	memset(&address, 0, sizeof(address));
	address.sun_family = AF_UNIX;
	strncpy(address.sun_path, socket_path, sizeof(address.sun_path) - 1);
	if (connect(fd, (struct sockaddr *)&address, sizeof(address)) < 0) {
		perror("DevBridge input: connect");
		return 1;
	}
	for (i = 1; i < argc; ++i) {
		int written = snprintf(request + used, sizeof(request) - used, "%s%s",
			i == 1 ? "" : " ", argv[i]);
		if (written < 0 || (size_t)written >= sizeof(request) - used) {
			fprintf(stderr, "DevBridge input: command is too long\n");
			return 1;
		}
		used += (size_t)written;
	}
	request[used++] = '\n';
	if (write(fd, request, used) != (ssize_t)used)
		return 1;
	count = read(fd, response, sizeof(response) - 1);
	if (count <= 0)
		return 1;
	response[count] = '\0';
	fputs(response, stdout);
	return strncmp(response, "ok", 2) == 0 ? 0 : 1;
}

static void usage(const char *program)
{
	fprintf(stderr,
		"usage: %s --daemon [socket]\n"
		"       %s tap BUTTON [milliseconds]\n"
		"       %s press|release BUTTON\n"
		"       %s release-all|status\n",
		program, program, program, program);
}

int main(int argc, char **argv)
{
	const char *configured = getenv("DEVBRIDGE_INPUT_SOCKET");
	if (configured && *configured)
		socket_path = configured;
	if (argc >= 2 && strcmp(argv[1], "--daemon") == 0)
		return run_daemon(argc >= 3 ? argv[2] : socket_path);
	if (argc < 2) {
		usage(argv[0]);
		return 2;
	}
	return send_command(argc, argv);
}
