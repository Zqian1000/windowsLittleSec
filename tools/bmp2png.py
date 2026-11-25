"""BMP(32bit) → PNG，纯标准库实现。"""
import struct, sys, zlib


def convert(src, dst):
    d = open(src, "rb").read()
    off = struct.unpack("<I", d[10:14])[0]
    w, h = struct.unpack("<ii", d[18:26])
    bpp = struct.unpack("<H", d[28:30])[0]
    stride = w * (bpp // 8)
    rows = []
    for j in range(abs(h)):
        s = off + j * stride
        row = d[s:s + w * (bpp // 8)]
        px = b"".join(bytes((row[i + 2], row[i + 1], row[i]))
                      for i in range(0, w * (bpp // 8), bpp // 8))
        rows.append(b"\x00" + px)
    raw = b"".join(rows) if h < 0 else b"".join(reversed(rows))

    def chunk(t, p):
        c = t + p
        return struct.pack(">I", len(p)) + c + struct.pack(">I", zlib.crc32(c))

    png = (b"\x89PNG\r\n\x1a\n"
           + chunk(b"IHDR", struct.pack(">IIBBBBB", w, abs(h), 8, 2, 0, 0, 0))
           + chunk(b"IDAT", zlib.compress(raw, 6))
           + chunk(b"IEND", b""))
    open(dst, "wb").write(png)
    print("png", dst, w, abs(h), bpp)


if __name__ == "__main__":
    convert(sys.argv[1], sys.argv[2])
