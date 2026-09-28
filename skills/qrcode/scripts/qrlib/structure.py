"""QR symbol structure: matrix, module roles, module -> codeword -> RS block mapping.

Error correction works per Reed-Solomon block and is counted in codewords (8 modules),
not in area. Everything the engine says about "error budget" comes from this mapping.
"""
import numpy as np
import segno
import segno.consts as C

# ISO/IEC 18004 misdecode-protection codewords (p) for small symbols
_P = {1: {'L': 3, 'M': 2, 'Q': 1, 'H': 1}, 2: {'L': 2}, 3: {'L': 1}}
EC_ORDER = ['L', 'M', 'Q', 'H']

# GF(256) tables for the RS self-check
_EXP = [0] * 512
_LOG = [0] * 256
_x = 1
for _i in range(255):
    _EXP[_i] = _x
    _LOG[_x] = _i
    _x <<= 1
    if _x & 0x100:
        _x ^= 0x11D
for _i in range(255, 512):
    _EXP[_i] = _EXP[_i - 255]


def _gmul(a, b):
    return 0 if a == 0 or b == 0 else _EXP[_LOG[a] + _LOG[b]]


class Structure:
    def __init__(self, payload, ec, version=None, mask=None, boost=False):
        qr = segno.make_qr(payload, error=ec, version=version, mask=mask, boost_error=boost)
        self.payload = payload
        self.qr = qr
        self.version = int(qr.version)
        self.ec = qr.error.upper()
        self.mask = int(qr.mask)
        self.mode = qr.mode
        self.n = 17 + 4 * self.version
        self.dark = np.array([list(r) for r in qr.matrix], dtype=np.uint8).astype(bool)
        # Roles are derived from ISO/IEC 18004 geometry (segno's verbose labels mark (8, n-9) as
        # format information although it carries data, so they are not used).
        self.role = self._roles()
        self._blocks()
        self._placement()
        self.central_alignment = self._central_alignment()

    def _roles(self):
        n = self.n
        role = np.full((n, n), 'data', dtype=object)
        for r0, c0 in [(0, 0), (0, n - 8), (n - 8, 0)]:
            role[r0:r0 + 8, c0:c0 + 8] = 'separator'
        for r0, c0 in [(0, 0), (0, n - 7), (n - 7, 0)]:
            role[r0:r0 + 7, c0:c0 + 7] = 'finder'
        for k in range(8, n - 8):
            role[6, k] = 'timing'
            role[k, 6] = 'timing'
        for c in [0, 1, 2, 3, 4, 5, 7, 8]:
            role[8, c] = 'format'
        for r in [0, 1, 2, 3, 4, 5, 7]:
            role[r, 8] = 'format'
        for c in range(n - 8, n):
            role[8, c] = 'format'
        for r in range(n - 8, n):
            role[r, 8] = 'format'  # includes the dark module at (n-8, 8)
        for (r, c) in self.alignment_centers():
            role[r - 2:r + 3, c - 2:c + 3] = 'alignment'
        if self.version >= 7:
            role[0:6, n - 11:n - 8] = 'version'
            role[n - 11:n - 8, 0:6] = 'version'
        return role

    # ---- RS block layout -------------------------------------------------
    def _blocks(self):
        groups = C.ECC[self.version][C.ERROR_MAPPING[self.ec]]
        self.block_data = []
        self.block_total = []
        for g in groups:
            for _ in range(g.num_blocks):
                self.block_data.append(g.num_data)
                self.block_total.append(g.num_total)
        self.ec_per_block = self.block_total[0] - self.block_data[0]
        p = _P.get(self.version, {}).get(self.ec, 0)
        self.capacity = (self.ec_per_block - p) // 2
        seq = []
        for i in range(max(self.block_data)):
            for b, k in enumerate(self.block_data):
                if i < k:
                    seq.append((b, 'd', i))
        for i in range(self.ec_per_block):
            for b in range(len(self.block_data)):
                seq.append((b, 'e', i))
        self.seq = seq
        self.total_codewords = len(seq)
        self.data_capacity_bytes = sum(self.block_data)

    def _placement(self):
        n = self.n
        free = self.role == 'data'
        order, j, up = [], n - 1, True
        while j > 0:
            if j == 6:
                j -= 1
            rows = range(n - 1, -1, -1) if up else range(n)
            for i in rows:
                for jj in (j, j - 1):
                    if free[i, jj]:
                        order.append((i, jj))
            up = not up
            j -= 2
        self.order = order
        self.cw_of = -np.ones((n, n), dtype=int)
        for p, (i, jj) in enumerate(order):
            if p // 8 < self.total_codewords:
                self.cw_of[i, jj] = p // 8
        self.block_of_cw = np.array([b for (b, _, _) in self.seq])

    def alignment_centers(self):
        if self.version < 2:
            return []
        pos = C.ALIGNMENT_POS[self.version - 2]
        n = self.n
        return [(r, c) for r in pos for c in pos
                if not ((r <= 8 and c <= 8) or (r <= 8 and c >= n - 9) or (r >= n - 9 and c <= 8))]

    def _central_alignment(self):
        if self.version < 7:
            return None
        pos = C.ALIGNMENT_POS[self.version - 2]
        if len(pos) % 2 == 0:
            return None
        c = pos[len(pos) // 2]
        return (c, c)

    def central_alignment_mask(self):
        m = np.zeros((self.n, self.n), bool)
        if self.central_alignment:
            r, c = self.central_alignment
            m[r - 2:r + 3, c - 2:c + 3] = True
        return m

    # ---- analysis helpers -----------------------------------------------
    def block_errors(self, wrong):
        """wrong: n x n bool of data modules read incorrectly -> errors per block."""
        cws = np.unique(self.cw_of[wrong & (self.cw_of >= 0)])
        per = np.zeros(len(self.block_data), dtype=int)
        for cw in cws:
            per[self.block_of_cw[cw]] += 1
        return per

    def static_budget(self, cleared, overlay=None):
        """Predict block errors for modules forced light (cleared) or of unknown appearance (overlay)."""
        wrong = cleared & self.dark & (self.role == 'data')
        if overlay is not None:
            wrong = wrong | (overlay & (self.role == 'data'))
        per = self.block_errors(wrong)
        return per, (per.max() / self.capacity if self.capacity else float('inf'))

    def self_check(self):
        """Read codewords back through the placement map and verify every RS block has zero syndromes."""
        mask_fn = [lambda i, j: (i + j) % 2 == 0, lambda i, j: i % 2 == 0, lambda i, j: j % 3 == 0,
                   lambda i, j: (i + j) % 3 == 0, lambda i, j: (i // 2 + j // 3) % 2 == 0,
                   lambda i, j: (i * j) % 2 + (i * j) % 3 == 0,
                   lambda i, j: ((i * j) % 2 + (i * j) % 3) % 2 == 0,
                   lambda i, j: ((i + j) % 2 + (i * j) % 3) % 2 == 0][self.mask]
        bits = [int(self.dark[i, j]) ^ int(mask_fn(i, j)) for (i, j) in self.order[:self.total_codewords * 8]]
        cws = [int(''.join(map(str, bits[k * 8:k * 8 + 8])), 2) for k in range(self.total_codewords)]
        blocks = [[None] * t for t in self.block_total]
        for cw, (b, kind, idx) in zip(cws, self.seq):
            blocks[b][idx if kind == 'd' else self.block_data[b] + idx] = cw
        for blk in blocks:
            for i in range(self.ec_per_block):
                a, s = _EXP[i], 0
                for cw in blk:
                    s = _gmul(s, a) ^ cw
                if s:
                    return False
        return True


def candidates(payload, ec='auto', version='auto'):
    """Distinct (version, ec) pairs, smallest symbol first; EC is raised as far as the version allows.

    A user-fixed EC is respected exactly (no boosting)."""
    if ec != 'auto':
        v = None if version == 'auto' else int(version)
        s = Structure(payload, ec, version=v)
        return [(s.version, s.ec)]
    out = []
    for level in EC_ORDER:
        try:
            s = Structure(payload, level, version=None if version == 'auto' else int(version), boost=True)
        except segno.DataOverflowError:
            continue
        if (s.version, s.ec) not in out:
            out.append((s.version, s.ec))
    out.sort(key=lambda t: (t[0], -EC_ORDER.index(t[1])))
    dedup, seen = [], set()
    for v, e in out:
        if v not in seen:
            dedup.append((v, e))
            seen.add(v)
    return dedup


def max_payload_bytes(version, ec):
    """Approximate byte-mode capacity of a symbol (used for 'shorten content to N bytes' advice)."""
    groups = C.ECC[version][C.ERROR_MAPPING[ec]]
    data = sum(g.num_blocks * g.num_data for g in groups)
    count_bits = 8 if version <= 9 else 16
    return (data * 8 - 4 - count_bits) // 8
