"""Quick shaded multi-view render of a Manifold using matplotlib."""
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
from mpl_toolkits.mplot3d.art3d import Poly3DCollection

VIEWS = [("Right side", 0, -90), ("Left side", 0, 90), ("Front 3/4", 20, -40),
         ("Rear 3/4", 25, 140), ("Top", 89, -90), ("Front", 5, 0)]


def render(items, path, views=VIEWS, size=(18, 11), grid=(2, 3)):
    """items: a Manifold, or a list of (Manifold, rgb) pairs."""
    if not isinstance(items, list):
        items = [(items, (0.16, 0.30, 0.62))]
    light = np.array([0.4, -0.5, 0.75])
    light /= np.linalg.norm(light)
    tris, cols = [], []
    for m, rgb in items:
        mesh = m.to_mesh()
        v = np.asarray(mesh.vert_properties)[:, :3]
        t = v[np.asarray(mesh.tri_verts)]
        n = np.cross(t[:, 1] - t[:, 0], t[:, 2] - t[:, 0])
        n /= np.maximum(np.linalg.norm(n, axis=1, keepdims=True), 1e-12)
        shade = 0.45 + 0.75 * np.clip(n @ light, 0, 1)
        tris.append(t)
        cols.append(np.clip(np.array(rgb)[None, :] * shade[:, None], 0, 1))
    tri, colors = np.concatenate(tris), np.concatenate(cols)
    v = tri.reshape(-1, 3)

    lo, hi = v.min(0), v.max(0)
    mid, span = (lo + hi) / 2, (hi - lo).max() / 2
    fig = plt.figure(figsize=size, facecolor="white")
    for i, (title, elev, azim) in enumerate(views):
        ax = fig.add_subplot(*grid, i + 1, projection="3d")
        ax.add_collection3d(Poly3DCollection(tri, facecolors=colors, linewidths=0))
        for setlim, c in zip((ax.set_xlim, ax.set_ylim, ax.set_zlim), mid):
            setlim(c - span, c + span)
        ax.set_box_aspect((1, 1, 1))
        ax.view_init(elev, azim)
        ax.set_axis_off()
        ax.set_title(title)
    plt.tight_layout()
    plt.savefig(path, dpi=90)
    plt.close(fig)
