"""Bounding-sphere cKDTree broad phase; exact narrow-phase checks remain authoritative."""
import numpy as np
from scipy.spatial import cKDTree
class SpatialCandidates:
    def __init__(self,bounds):
        self.bounds=bounds
        self.centers=np.asarray([(np.asarray(a)+np.asarray(b))/2 for a,b in bounds])
        self.radius=max((float(np.linalg.norm(np.subtract(b,a))/2) for a,b in bounds),default=0.)
        self.tree=cKDTree(self.centers) if bounds else None
    def query(self,a,b,margin=0.):
        if self.tree is None:return []
        center=(np.asarray(a)+np.asarray(b))/2
        radius=float(np.linalg.norm(np.subtract(b,a))/2)+self.radius+margin
        return self.tree.query_ball_point(center,radius+1e-8)
