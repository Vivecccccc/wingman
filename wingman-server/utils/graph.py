import heapq
import numpy as np
import igraph as ig

from itertools import pairwise, accumulate
from typing import Dict, List, Optional

from .models import Path

class HallOfFame:
    def __init__(self, max_size):
        self.max_size = max_size
        self.elements = []

    def push(self, element: tuple[float, int]):
        if len(self.elements) < self.max_size:
            heapq.heappush(self.elements, element)
        else:
            heapq.heappushpop(self.elements, element)

    def get_values(self) -> List[tuple[float, int]]:
        return sorted(self.elements, key=lambda x: x[0], reverse=True)
    
    def get_sim(self) -> List[float]:
        return [x[0] for x in self.get_values()]
    
    def get_indices(self) -> List[int]:
        return [x[1] for x in self.get_values()]
    
def propagate_similarities(g: ig.Graph, sim: np.ndarray, damping_factor=0.85, depth_penalty=1, elem_top_k=5):
    propagated_scores = {node.index: 0 for node in g.vs.select(type="page")}
    hofs = {node.index: HallOfFame(max_size=elem_top_k) for node in g.vs.select(type="page")}

    def is_valid_path(path):
        pairs = pairwise(path)
        for p in pairs:
            edge = g.es[g.get_eid(p[1], p[0])]
            if edge['type'] == 'IS_CHILD':
                continue
            return False
        return True

    for element in g.vs.select(type="element"):
        if not element['text'] or element['text'] == 'None':
            continue
        paths = g.get_all_simple_paths(element, to=g.vs.select(type='page'), mode='IN')
        for path in paths:
            if not is_valid_path(path):
                continue
            page = g.vs[path[-1]]
            depth = len(path) - 1
            propagated_score = float(sim[0, element.index] * (damping_factor ** depth) * (depth_penalty ** depth))
            hofs[page.index].push((propagated_score, element.index))
            propagated_scores[page.index] += propagated_score

    return propagated_scores, hofs

def rank_pages(g: ig.Graph, sim: np.ndarray, scorecards: Dict[str, float], alpha=0.5):    
    final_scores = {}
    for page in g.vs.select(type="page"):
        own_similarity = float(sim[0, page.index])
        propagated_score = scorecards[page.index]

        final_score = alpha * own_similarity + (1 - alpha) * propagated_score
        final_scores[page.index] = final_score
    
    page_rank = sorted(final_scores.items(), key=lambda x: x[1], reverse=True)
    return page_rank

def page_rank_sample(sorted_page_rank: List[tuple[int, float]], top_p=0.1, top_k=5) -> List[tuple[int, float]]:
    acc = accumulate((x[1] for x in sorted_page_rank))
    for i, x in enumerate(acc):
        if x >= top_p or i >= top_k:
            break
    subset = sorted_page_rank[:i]
    for x in subset:
        if x[0] == 1:
            return sorted_page_rank[:(i + 1)]
    return subset

def get_all_children(g: ig.Graph, node: ig.Vertex, children=None):
    if children is None:
        children = []
    for edge in g.es.select(_source=node.index):
        if edge['type'] == 'IS_CHILD':
            child = g.vs[edge.target]
            children.append(child)
            get_all_children(g, child, children)
    return children

def fold_path(g: ig.Graph, path: List[int], start_page: int) -> List[Path]:
    page_stack = [start_page]
    warp = []
    for i, p in enumerate(path):
        el_in_src = None
        dst_page = None
        if g.vs[p]['type'] == 'page':
            src_page = page_stack.pop()
            el_in_src = path[i - 1]
            dst_page = p
            page_stack.append(dst_page)
            warp.append(Path(graph=g, srcPage=src_page, elInSrc=el_in_src, dstPage=dst_page))
    return warp

def bulldozer(g: ig.Graph, current_page_id: int, target_page_ids: List[int]) -> Dict[tuple[int, int, int], Path]:
    warps = {}
    current_page = g.vs[current_page_id]
    target_pages = [g.vs[i] for i in target_page_ids if i != current_page_id]
    current_elements = get_all_children(g, current_page)
    for elem in current_elements:
        paths = g.get_all_shortest_paths(elem, to=target_pages, mode='OUT')
        for path in paths:
            folded_path = fold_path(g, path, current_page_id)
            for part in folded_path:
                warps[(part.srcPage, part.elInSrc, part.dstPage)] = part
    return warps

def bulldozer_single(g: ig.Graph, current_page_id: int, target_page_id: int) -> Dict[tuple[int, int, int], Path]:
    warps = {}
    current_page = g.vs[current_page_id]
    target_page = g.vs[target_page_id]
    path = g.get_shortest_path(current_page, to=target_page, mode='OUT')
    
    def _fold_path(path: List[int]):
        breakpoints = [i for i, n in enumerate(path) if g.vs[n]['type'] == 'page']
        from itertools import pairwise
        for start, end in pairwise(breakpoints):
            yield Path(graph=g, srcPage=path[start], elInSrc=path[end - 1], dstPage=path[end])
    
    folded_path = _fold_path(path)
    for part in folded_path:
        warps[(part.srcPage, part.elInSrc, part.dstPage)] = part
    return warps