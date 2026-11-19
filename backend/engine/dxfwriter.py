"""Minimal ASCII DXF (R12) writer: LINE, CIRCLE, TEXT, closed polylines. No dependency, readable by AutoCAD/LibreCAD."""
class DXF:
    def __init__(self):
        self.e, self.layers = [], {"0"}

    def _l(self, layer):
        self.layers.add(layer)

    def line(self, x1, y1, x2, y2, layer="0"):
        self._l(layer); self.e.append(f"0\nLINE\n8\n{layer}\n10\n{x1:.4f}\n20\n{y1:.4f}\n30\n0\n11\n{x2:.4f}\n21\n{y2:.4f}\n31\n0\n")

    def poly(self, pts, layer="0", closed=True):
        seq = list(pts) + ([pts[0]] if closed else [])
        for a, b in zip(seq, seq[1:]):
            self.line(*a, *b, layer=layer)

    def circle(self, x, y, r, layer="0"):
        self._l(layer); self.e.append(f"0\nCIRCLE\n8\n{layer}\n10\n{x:.4f}\n20\n{y:.4f}\n30\n0\n40\n{r:.4f}\n")

    def text(self, x, y, h, s, layer="0"):
        s = str(s).replace("\n", " ")[:250]
        self._l(layer); self.e.append(f"0\nTEXT\n8\n{layer}\n10\n{x:.4f}\n20\n{y:.4f}\n30\n0\n40\n{h:.4f}\n1\n{s}\n")

    def render(self, insunits=6) -> str:
        tbl = "".join(f"0\nLAYER\n2\n{l}\n70\n0\n62\n7\n6\nCONTINUOUS\n" for l in sorted(self.layers))
        return (f"0\nSECTION\n2\nHEADER\n9\n$ACADVER\n1\nAC1009\n9\n$INSUNITS\n70\n{insunits}\n0\nENDSEC\n"
                f"0\nSECTION\n2\nTABLES\n0\nTABLE\n2\nLAYER\n70\n{len(self.layers)}\n{tbl}0\nENDTAB\n0\nENDSEC\n"
                f"0\nSECTION\n2\nENTITIES\n{''.join(self.e)}0\nENDSEC\n0\nEOF\n")
