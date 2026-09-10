/*
 * udpsend - send one UDP datagram, read from stdin.
 *
 * BusyBox nc on these devices is built without FEATURE_NETCAT_EXTRA, so it
 * has no -u flag and cannot send UDP at all, and neither device ships socat,
 * python or perl. The A30 has no bash either, so the /dev/udp redirection
 * trick is unavailable. This exists solely so the discovery beacon has a
 * transport.
 *
 * usage: udpsend <address> <port>
 *
 * The destination is normally a multicast group. Multicast is used rather
 * than a broadcast address because it needs no SO_BROADCAST, though this
 * program sets that option anyway so a broadcast destination also works.
 */

#include <arpa/inet.h>
#include <netinet/in.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/socket.h>
#include <unistd.h>

int main(int argc, char **argv)
{
	if (argc != 3) {
		fprintf(stderr, "usage: %s <address> <port>\n", argv[0]);
		return 2;
	}

	struct sockaddr_in destination;
	memset(&destination, 0, sizeof(destination));
	destination.sin_family = AF_INET;
	destination.sin_port = htons((unsigned short)atoi(argv[2]));
	if (inet_pton(AF_INET, argv[1], &destination.sin_addr) != 1) {
		fprintf(stderr, "udpsend: bad address %s\n", argv[1]);
		return 2;
	}

	char buffer[1400];
	size_t length = fread(buffer, 1, sizeof(buffer), stdin);
	if (length == 0)
		return 0;

	int fd = socket(AF_INET, SOCK_DGRAM, 0);
	if (fd < 0) {
		perror("udpsend: socket");
		return 1;
	}

	int on = 1;
	setsockopt(fd, SOL_SOCKET, SO_BROADCAST, &on, sizeof(on));

	/* Keep the announcement on the local link. */
	unsigned char ttl = 1;
	setsockopt(fd, IPPROTO_IP, IP_MULTICAST_TTL, &ttl, sizeof(ttl));

	ssize_t sent = sendto(fd, buffer, length, 0,
	                      (struct sockaddr *)&destination, sizeof(destination));
	if (sent < 0) {
		perror("udpsend: sendto");
		close(fd);
		return 1;
	}

	close(fd);
	return 0;
}
