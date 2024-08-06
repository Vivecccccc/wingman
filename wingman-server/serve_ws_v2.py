import asyncio
import json
import random
import time
import logging
import openai

from json import JSONDecodeError
from typing import List, Dict, Union
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException
from pydantic import ValidationError

from utils.models import GraphContextForWebsocket, StageInstructionGenerationResponseForWebsocket, StageIntentDetectionRequestItem, StageIntentDetectionResponse, StageInstructionGenerationRequest, StageInstructionGenerationResponse
from utils.get_response import Stage, get_response
from constants import GRAPH_PARAMS, SEMANTIC_PARAMS, LLM_PARAMS, PAGE_NODE_MAPPING
from compute import read_graph, prepare_library, get_context_stage_1, get_context_stage_2, get_relevances

# suppress runtime warnings
import warnings
warnings.filterwarnings("ignore")

logging.basicConfig(filename="main.log", level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

app = FastAPI()

try:
    g = read_graph(GRAPH_PARAMS['graph_path'])
    lib = prepare_library(g, SEMANTIC_PARAMS.get('lib_path', None))
except Exception as e:
    logger.error(f"Error in reading graph or preparing library: {e}")
    raise e

async def send_context(stage: Stage, id: int, context_data: Union[List[StageIntentDetectionRequestItem], StageIntentDetectionResponse], websocket: WebSocket):
    if stage == Stage.INTENT_DETECTION:
        relevant_pages = [item.relevant_page for item in context_data]
    elif stage == Stage.INSTRUCTION_GENERATION:
        relevant_pages = [context_data.ends_at]
    stage_type = "stage_1" if stage == Stage.INTENT_DETECTION else "stage_2"
    try:
        payload = GraphContextForWebsocket(relevant_pages=relevant_pages, type=stage_type, id=id)
        await websocket.send_text(payload.json())
    except Exception as e:
        logging.error(f"Error in sending {stage_type} context data: {e}")
        raise HTTPException(status_code=500, detail=f"Error in sending {stage_type} context data: {e}")

def stringify_context(stage: Stage, context_data: Union[List[StageIntentDetectionRequestItem], StageInstructionGenerationRequest]) -> str:
    if stage == Stage.INTENT_DETECTION and isinstance(context_data, list):
        return json.dumps([item.dict() for item in context_data])
    elif stage == Stage.INSTRUCTION_GENERATION and isinstance(context_data, StageInstructionGenerationRequest):
        return context_data.json()
    else:
        raise ValueError(f"Invalid stage or context data")

@app.websocket("/navigate_ws")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    try:
        while True:
            data = await websocket.receive_text()
            request = json.loads(data)
            id = request['id']
            user_query = request['user_query']
            page_history = request['page_history']
            user_current_page_name = page_history[-1]

            if not user_query or not user_current_page_name:
                logger.error("User query and current page name must be provided")
                raise HTTPException(status_code=400, detail="User query and current page name must be provided")
            
            relevances_dict = get_relevances(g, lib, user_query)
            # stage 1
            try:
                context_data_stage_1 = get_context_stage_1(g, relevances_dict)
                await send_context(Stage.INTENT_DETECTION, id, context_data_stage_1, websocket)
                await asyncio.sleep(0)
                context_data_stage_1 = stringify_context(Stage.INTENT_DETECTION, context_data_stage_1)
                response_stage_1 = await get_response(Stage.INTENT_DETECTION, user_query, user_current_page_name, context_data_stage_1)
            except Exception as e:
                logger.error(f"Error in getting response for stage 1: {e}")
                raise HTTPException(status_code=500, detail=f"Error in getting response for stage 1: {e}")
            # stage 2
            try:
                await asyncio.sleep(0)
                await send_context(Stage.INSTRUCTION_GENERATION, id, response_stage_1, websocket)
                await asyncio.sleep(0)
                start_page = PAGE_NODE_MAPPING[user_current_page_name]
                final_destination = PAGE_NODE_MAPPING[response_stage_1.ends_at]
                context_data_stage_2 = stringify_context(Stage.INSTRUCTION_GENERATION, 
                                                         get_context_stage_2(g, start_page, final_destination, relevances_dict))
                response_stage_2 = await get_response(Stage.INSTRUCTION_GENERATION, user_query, user_current_page_name, context_data_stage_2, response_stage_1.ends_at)
            except Exception as e:
                logger.error(f"Error in getting response for stage 2: {e}")
                raise HTTPException(status_code=500, detail=f"Error in getting response for stage 2: {e}")
            try:
                response_fow_ws = StageInstructionGenerationResponseForWebsocket(**response_stage_2.dict(), id=id, type="final")
                await websocket.send_text(response_fow_ws.json())
            except Exception as e:
                logger.error(f"Error in sending final response: {e}")
                raise HTTPException(status_code=500, detail=f"Error in sending final response: {e}")
    except WebSocketDisconnect:
        logger.info("Client disconnected")
    finally:
        await websocket.close()

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)