set -eu
umask 077
test ! -L /executable/private
mkdir -p /executable/private
chmod 0700 /executable/private
test "$(stat -c '%u:%g:%a' /executable/private)" = "$(id -u):$(id -g):700"
temporary=/executable/private/.caddy.$$
trap 'rm -f "$temporary"' EXIT HUP INT TERM
cp /usr/bin/caddy "$temporary"
chmod 0500 "$temporary"
cmp -s /usr/bin/caddy "$temporary"
test "$(stat -c '%u:%g:%a' "$temporary")" = "$(id -u):$(id -g):500"
capabilities=$(getcap "$temporary")
test -z "$capabilities"
mv -f "$temporary" /executable/private/caddy
