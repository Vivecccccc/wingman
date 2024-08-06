import os
import json
import logging
import numpy as np
import igraph as ig

from typing import Dict, List, Optional

from constants import SEMANTIC_PARAMS, GRAPH_PARAMS, PAGE_NODE_MAPPING
from utils.models import Path, StageInstructionGenerationRequest, StageIntentDetectionRequestItem, Linkage, RelevantElement
from utils.semantic import get_semantic_embedding, get_sim_vec
from utils.graph import propagate_similarities, rank_pages, page_rank_sample, bulldozer, bulldozer_single

embedding_dim = SEMANTIC_PARAMS['dim']
damping_factor = GRAPH_PARAMS['damping_factor']
depth_penalty = GRAPH_PARAMS['depth_penalty']
alpha = GRAPH_PARAMS['alpha']
pagerank_sample_top_p = GRAPH_PARAMS['pagerank_sample_top_p']
pagerank_sample_top_k = GRAPH_PARAMS['pagerank_sample_top_k']
hof_top_k = GRAPH_PARAMS['hof_top_k']

logger = logging.getLogger(__name__)

def read_graph(file_path: str) -> ig.Graph:
    logger.info(f"Reading graph from {file_path}")
    g = ig.Graph.Read(file_path)
    return g

def prepare_library(g: ig.Graph, lib_path: Optional[str] = None) -> np.ndarray:
    shape = (len(g.vs), embedding_dim)
    if lib_path and os.path.exists(lib_path):
        logger.info(f"Loading library from {lib_path}")
        lib = np.load(lib_path)
        if lib.shape != shape:
            err = f"Shape mismatch from {lib_path}: {lib.shape} != {shape}"
            logger.error(err)
            raise ValueError(err)
        return lib
    logger.info(f"Preparing new library of shape {shape}")
    lib = np.empty(shape)
    for i, v in enumerate(g.vs):
        if i != v.index:
            err = f"Index mismatch: {i} != {v.index}"
            logger.error(err)
            raise ValueError(err)
        emb = get_semantic_embedding(v['text'])
        lib[i] = emb
    if lib_path:
        np.save(lib_path, lib)
    return lib

def retrieve_relevance(g: ig.Graph, query: str, lib: np.ndarray):
    sim_vec = get_sim_vec(query, lib)
    # propagate similarities and rank pages
    scorecards, hofs = propagate_similarities(g, sim_vec, damping_factor=damping_factor, depth_penalty=depth_penalty, elem_top_k=hof_top_k)
    page_rank = rank_pages(g, sim_vec, scorecards, alpha=alpha)
    
    # contribute similarity of top element to its page
    for i, (idx, score) in enumerate(page_rank):
        goat = sim_vec[0, hofs[idx].get_indices()[0]]
        page_rank[i] = (idx, score + goat)

    # softmax page rank scores
    page_rank_scores = np.array([x[1] for x in page_rank])
    page_rank_scores = np.exp(page_rank_scores) / np.sum(np.exp(page_rank_scores))
    page_rank = sorted([(idx, sc) for (idx, _), sc in zip(page_rank, page_rank_scores)], key=lambda x: x[1], reverse=True)

    # sample with top_p or top_k (whichever comes first)
    relevant_pages = page_rank_sample(page_rank, top_p=pagerank_sample_top_p, top_k=pagerank_sample_top_k)
    # get relevant elements of top pages
    relevant_elements = [hofs[idx].get_indices() for idx, _ in relevant_pages]

    return relevant_pages, relevant_elements

def get_context(g: ig.Graph,
                lib: np.ndarray,
                query: str,
                current_page_name: str,
                return_str: bool = True) -> str | Dict[str, List]:
    relevant_pages, relevant_elements = retrieve_relevance(g, query, lib)
    logger.info(f"User's query: {query}; User's current page: {current_page_name}")
    # post-process
    relevant_pages = [x[0] for x in relevant_pages]
    logger.info(f"Relevant pages: {[g.vs[x]['text'] for x in relevant_pages]}")
    current_page_id = PAGE_NODE_MAPPING.get(current_page_name, None)
    if current_page_id is None:
        raise ValueError(f"Page not found: {current_page_name}")
    
    paths = {}
    # 1. get paths from current page to relevant pages
    paths.update(bulldozer(g, current_page_id, relevant_pages))
    if not paths:
        backup_header_id = PAGE_NODE_MAPPING.get('Header of the page', None)
        if backup_header_id is None:
            raise ValueError("Header of the page not found")
        paths.update(bulldozer(g, backup_header_id, relevant_pages))
        if not paths:
            raise ValueError("No path from current page to relevant pages")
    # 2. get all paths from target pages to relevant elements
    for src_page, els in zip(relevant_pages, relevant_elements):
        for el in els:
            paths[(src_page, el, None)] = Path(graph=g, srcPage=src_page, elInSrc=el)
    # 3. remove redundancy
    keys = list(paths.keys())
    for (s, e, d) in keys:
        if d is not None:
           paths.pop((s, e, None), None)

    # serialize
    result = {"linkage": [], "relevance": []}
    for _, v in paths.items():
        if v.dstPage is None:
            result["relevance"].append(v.custom_dict())
        else:
            result["linkage"].append(v.custom_dict())
    if not return_str:
        return result
    return json.dumps(result)

def get_relevances(g: ig.Graph,
                   lib: np.ndarray,
                   query: str) -> Dict[int, List[int]]:
    relevant_pages, relevant_elements = retrieve_relevance(g, query, lib)
    relevances = {}
    for (page, _), page_elem in zip(relevant_pages, relevant_elements):
        relevances[page] = page_elem
    return relevances

def get_context_stage_1(g: ig.Graph, relevances: Dict[int, List[int]]) -> List[StageIntentDetectionRequestItem]:
    stage_0_request_list = []
    for page_idx in relevances:
        if page_idx in (0, 1):
            continue
        page_name = g.vs[page_idx]['text']
        relevant_elements = []
        for elem_idx in relevances[page_idx]:
            elem_name = g.vs[elem_idx]['text']
            relevant_elements.append(elem_name)
        stage_0_request_list.append(StageIntentDetectionRequestItem(relevant_page=page_name, relevant_elements=relevant_elements))
    return stage_0_request_list

def get_context_stage_2(g: ig.Graph,
                        start: int,
                        destination: int,
                        relevances_dict: Dict[int, List[int]]) -> StageInstructionGenerationRequest:
    paths: Dict[tuple[int, int, Optional[int]], Path] = {}
    paths.update(bulldozer_single(g, start, destination))
    if not paths:
        backup_header_id = PAGE_NODE_MAPPING.get('Header of the page', None)
        if backup_header_id is None:
            raise ValueError("Header of the page not found")
        paths.update(bulldozer_single(g, backup_header_id, destination))
        if not paths:
            raise ValueError("No path from current page to relevant pages")
    relevant_elements = relevances_dict.get(destination, [])
    # if not relevant_elements:
    #     raise ValueError("No relevant elements from destination page found")
    for el in relevant_elements:
        paths[(destination, el, None)] = Path(graph=g, srcPage=destination, elInSrc=el)
    # get result
    step_count = 1
    linkages: List[Linkage] = []
    relevances: List[RelevantElement] = []
    for _, path in paths.items():
        if path.dstPage is not None:

            source_page = g.vs[path.srcPage]['text']
            target_page = g.vs[path.dstPage]['text']
            portal_text = g.vs[path.elInSrc]['text']
            portal_selector = " > ".join([g.vs[x]['selector'] for x in path.pathToEl])
            
            portal = RelevantElement(text=portal_text, selector=portal_selector)
            linkages.append(Linkage(step=step_count,
                                    src_page=source_page,
                                    dst_page=target_page,
                                    portal=portal))
            step_count += 1
        else:
            element_text = g.vs[path.elInSrc]['text']
            element_selector = " > ".join([g.vs[x]['selector'] for x in path.pathToEl])
            relevant_element = RelevantElement(text=element_text, selector=element_selector)
            relevances.append(relevant_element)
    return StageInstructionGenerationRequest(linkages=linkages, relevances=relevances)