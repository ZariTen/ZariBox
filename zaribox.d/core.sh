VERSION="0.1.2"
ZARIBOX_DIR="${HOME}/.local/share/zaribox"
CACHE_DIR="${ZARIBOX_DIR}/cache"

mkdir -p "$CACHE_DIR"

RED=$'\033[0;31m'
GRN=$'\033[0;32m'
YLW=$'\033[0;33m'
BLU=$'\033[0;34m'
MAG=$'\033[0;35m'
CYN=$'\033[0;36m'
BOLD=$'\033[1m'
DIM=$'\033[2m'
RST=$'\033[0m'

log()  { printf '%s\n' "${BLU}${BOLD}[zaribox]${RST} $*"; }
ok()   { printf '%s\n' "${GRN}${BOLD}  ok${RST} $*"; }
warn() { printf '%s\n' "${YLW}${BOLD}  warn${RST} $*"; }
err()  { printf '%s\n' "${RED}${BOLD}  error${RST} $*" >&2; }
step() { printf '%s\n' "${MAG}  ->${RST} $*"; }