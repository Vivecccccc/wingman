import json
import igraph as ig

from typing import List, Optional
from pydantic import BaseModel, root_validator, validator

from constants import PAGE_NODE_MAPPING

def validate_page_str(page_str: str):
    if page_str not in PAGE_NODE_MAPPING:
        raise ValueError(f"page {page_str} not in PAGE_NODE_MAPPING")
    return page_str

def validate_page_idx(page_idx: int):
    if page_idx not in PAGE_NODE_MAPPING.values():
        raise ValueError(f"page index {page_idx} not in PAGE_NODE_MAPPING")
    return page_idx

class Path(BaseModel):
    graph: ig.Graph
    srcPage: int
    elInSrc: int
    dstPage: Optional[int]
    pathToEl: List[int] = []

    def custom_dict(self):
        new_dict = {}
        if self.dstPage is None:
            new_dict['relevant_elem'] = self.graph.vs[self.elInSrc]['text']
            new_dict['target_page'] = self.graph.vs[self.srcPage]['text']
        else:
            new_dict["source_page"] = self.graph.vs[self.srcPage]['text']
            new_dict["linking_elem"] = self.graph.vs[self.elInSrc]['text']
            new_dict['target_page'] = self.graph.vs[self.dstPage]['text']
        new_dict["element_selector"] = " > ".join([self.graph.vs[x]['selector'] for x in self.pathToEl])
        new_dict["element_interactive"] = bool(self.graph.vs[self.elInSrc]["isInteractive"])
        return new_dict
    
    def custom_json(self):
        return json.dumps(self.custom_dict())

    @root_validator
    def validate_src_page(cls, values):
        g = values.get('graph')
        src = values.get('srcPage')
        if src is not None and g.vs[src]['type'] != 'page':
            raise ValueError('source node must be a page node')
        return values

    @root_validator
    def validate_dst_page(cls, values):
        g = values.get('graph')
        dst = values.get('dstPage')
        if dst is not None and g.vs[dst]['type'] != 'page':
            raise ValueError('destination node must be a page node')
        return values

    @root_validator
    def validate_el_in_src(cls, values):
        g = values.get('graph')
        el = values.get('elInSrc')
        if el is not None and g.vs[el]['type'] != 'element':
            raise ValueError('element in source page must be an element node')
        return values

    @root_validator
    def compute_path_to_el(cls, values):
        if 'pathToEl' in values and values['pathToEl']:
            return values

        g = values.get('graph')
        src = values.get('srcPage')
        el = values.get('elInSrc')

        paths = g.get_all_simple_paths(g.vs[src], to=g.vs[el], mode='OUT')
        if not paths:
            raise ValueError('no path from page root to element found')

        from itertools import pairwise
        valid_paths: List[List[int]] = []
        for path in paths:
            is_valid = True
            pairs = pairwise(path)
            for pair in pairs:
                edge = g.es[g.get_eid(*pair)]
                if edge['type'] != 'IS_CHILD':
                    is_valid = False
                    break
            if is_valid:
                valid_paths.append(path)
        if not valid_paths:
            raise ValueError('no valid path from page root to element found')
        values['pathToEl'] = min(valid_paths, key=len)
        return values
    
    @root_validator
    def external_edge_type_must_be_links_to(cls, values):
        g = values.get('graph')
        el = values.get('elInSrc')
        dst = values.get('dstPage')
        if dst is None:
            return values
        e = g.es[g.get_eid(el, dst, error=False)]
        if e == -1 or e['type'] != 'LINKS_TO':
            raise ValueError('external edge type must be LINKS_TO')
        return values
    
    class Config:
        underscore_attrs_are_private = True
        arbitrary_types_allowed = True

class UserRequest(BaseModel):
    user_query: str
    page_history: List[str]

class ResponseLinkage(BaseModel):
    linking_elem: str
    element_selector: str
    element_interactive: bool

class ResponseRelevantElement(BaseModel):
    relevant_elem: str
    element_selector: str
    element_interactive: bool

class ResponsePathStep(BaseModel):
    step: int
    source_page: str
    target_page: str
    linkage: ResponseLinkage
    relevant_elements: List[ResponseRelevantElement]
    instruction: str

class LlmResponse(BaseModel):
    user_current_page: str
    user_target_page: str
    path: List[ResponsePathStep]
    summary: str

class LlmResponseForWebsocket(LlmResponse):
    id: int
    type: str

class GraphContextForWebsocket(BaseModel):
    relevant_pages: List[str]
    type: str
    id: Optional[int]

    @validator('relevant_pages')
    def validate_relevant_pages(cls, v):
        for page in v:
            try:
                validate_page_str(page)
            except ValueError as e:
                raise ValueError(f"Invalid page: {e}")
        return v

class RelevantElement(BaseModel):
    text: str
    selector: str

class RelevantElementAddOn(RelevantElement):
    hint: str

class Linkage(BaseModel):
    step: int
    src_page: str
    dst_page: str
    portal: RelevantElement

    @validator('src_page')
    def validate_src_page(cls, v):
        return validate_page_str(v)
    
    @validator('dst_page')
    def validate_dst_page(cls, v):
        return validate_page_str(v)

class LinkageAddOn(Linkage):
    instruction: str

class StageIntentDetectionRequestItem(BaseModel):
    relevant_page: str
    relevant_elements: List[str]

    @validator('relevant_page')
    def validate_relevant_page(cls, v):
        return validate_page_str(v)

class StageInstructionGenerationRequest(BaseModel):
    linkages: List[Linkage]
    relevances: List[RelevantElement]

class StageIntentDetectionResponse(BaseModel):
    reasoning: str
    starts_from: str
    ends_at: str

    @validator('starts_from')
    def validate_starts_from(cls, v):
        return validate_page_str(v)
    
    @validator('ends_at')
    def validate_ends_at(cls, v):
        return validate_page_str(v)

class StageInstructionGenerationResponse(BaseModel):
    user_current_page: str
    user_target_page: str
    linkages: List[LinkageAddOn]
    relevances: List[RelevantElementAddOn]

    @validator('user_current_page')
    def validate_user_current_page(cls, v):
        return validate_page_str(v)
    
    @validator('user_target_page')
    def validate_user_target_page(cls, v):
        return validate_page_str(v)
    
class StageInstructionGenerationResponseForWebsocket(StageInstructionGenerationResponse):
    id: int
    type: str