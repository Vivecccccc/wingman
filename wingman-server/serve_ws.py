import asyncio
import json
import random
import time
import logging
import openai

from json import JSONDecodeError
from typing import List, Dict
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException
from pydantic import ValidationError

from constants import GRAPH_PARAMS, SEMANTIC_PARAMS, LLM_PARAMS
from compute import read_graph, prepare_library, get_context
from utils.models import LlmResponse, LlmResponseForWebsocket, GraphContextForWebsocket

logging.basicConfig(filename="main.log", level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

app = FastAPI()

sys_prompt = LLM_PARAMS['system_prompt']
default_vendor = LLM_PARAMS.get('default_vendor', "mistral")

try:
    g = read_graph(GRAPH_PARAMS['graph_path'])
    lib = prepare_library(g, SEMANTIC_PARAMS.get('lib_path', None))
except Exception as e:
    logger.error(f"Error in reading graph or preparing library: {e}")
    raise e

async def retrieve_context_data(query: str, user_current_page: str, websocket: WebSocket) -> str:
    try:
        context_data: Dict[str, List] = get_context(g, lib, query, user_current_page, return_str=False)
        pages = set([x['target_page'] for x in context_data['linkage']])
        pages.update([x['target_page'] for x in context_data['relevance']])
        payload_interim = GraphContextForWebsocket(relevant_pages=sorted(list(pages)), type="interim")
        await websocket.send_text(payload_interim.json())
    except Exception as e:
        logger.error(f"Error in generating context data: {e}")
        raise HTTPException(status_code=500, detail=f"Error in generating context data: {e}")
    return json.dumps(context_data)

async def _get_response(attempt: int, sys_prompt: str, user_prompt: str, vendor: str = default_vendor):
    temperature = min(LLM_PARAMS.get('temperature', 0.0) + 0.2 * attempt, 1.0)
    if vendor == "alibaba-intl":
        from dashscope import Generation
        import dashscope
        vendor_config = LLM_PARAMS['config_alibaba-intl']
        dashscope.base_http_api_url = vendor_config['endpoint']
        dashscope.api_key = vendor_config['api_key']
        model = vendor_config['model']

        response = Generation.call(model=model,
                                   messages=[
                                        {"role": "system", "content": sys_prompt},
                                        {"role": "user", "content": user_prompt}
                                   ],
                                   temperature=temperature,
                                   top_p=LLM_PARAMS.get('top_p', None),
                                   max_tokens=LLM_PARAMS.get('max_tokens', None),
                                   stream=False,
                                   result_format='message'
                                )
    else:
        if vendor == "alibaba":
            vendor_config = LLM_PARAMS['config_alibaba']
        elif vendor == "deepseek":
            vendor_config = LLM_PARAMS['config_deepseek']
        elif vendor == "mistral":
            vendor_config = LLM_PARAMS['config_mistral']
        else:
            raise NotImplementedError(f"Vendor {vendor} not implemented, please choose from 'alibaba(-intl)' or 'deepseek', or 'mistral'")
        api_key = vendor_config['api_key']
        endpoint = vendor_config['endpoint']
        model = vendor_config['model']

        client = openai.OpenAI(api_key=api_key, base_url=endpoint)
        response = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": sys_prompt},
                {"role": "user", "content": user_prompt},
            ],
            temperature=temperature,
            top_p=LLM_PARAMS.get('top_p', None),
            max_tokens=LLM_PARAMS.get('max_tokens', None),
            stream=False,
            response_format={"type": "json_object"}
        )
    return response

def locate_json_content(text: str):
    text = text.strip()
    # the json might be wrapped in ```
    if text.startswith("```") and text.endswith("```"):
        text = text[3:-3]
        if text.startswith("json"):
            text = text[4:]
    text = text.strip()
    return text

async def get_response(query: str, user_current_page_name: str, context_data: str, vendor: str = default_vendor):
    user_prompt = f"""
    User Query: {query}
    User Current Page: {user_current_page_name}
    Context Data: {context_data}
    """

    max_retries = LLM_PARAMS.get('max_retries', 3)
    initial_delay = 1
    start_time = time.time()
    
    for attempt in range(max_retries):
        try:
            response = await _get_response(attempt, sys_prompt, user_prompt, vendor)
            try:
                if vendor == "alibaba-intl":
                    should_legit = locate_json_content(response.output.choices[0].message.content)
                    message_content = json.loads(should_legit)
                    input_tokens = response.usage.input_tokens
                    output_tokens = response.usage.output_tokens
                else:
                    should_legit = locate_json_content(response.choices[0].message.content)
                    message_content = json.loads(should_legit)
                    input_tokens = response.usage.prompt_tokens
                    output_tokens = response.usage.completion_tokens
                llm_response = LlmResponse(**message_content)
            except (JSONDecodeError, ValidationError) as e:
                logger.error(f"Malformed response received at attempt {attempt + 1}/{max_retries}: {e}")
                if attempt < max_retries - 1:
                    delay = initial_delay * (2 ** attempt) + random.uniform(0, 1)
                    time.sleep(delay)
                    continue
                else:
                    raise HTTPException(status_code=500, detail=f"Malformed response received, retries exhausted: {e}")
            break
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Error in sending request: {e}")
    # usage of tokens
    time_elapsed = time.time() - start_time
    logger.info(f"Total attempts {attempt + 1}/{max_retries}; Request elapsed: {time_elapsed}")
    logger.info(f"Total tokens: {input_tokens + output_tokens}; Input tokens: {input_tokens}; Output tokens: {output_tokens}")
    return llm_response

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
            
            try:
                context_data = await retrieve_context_data(user_query, user_current_page_name, websocket)
                await asyncio.sleep(0)
                response = await get_response(user_query, user_current_page_name, context_data)
                response_for_ws = LlmResponseForWebsocket(**response.dict(), id=id, type="final")
                await websocket.send_text(response_for_ws.json())
            except Exception as e:
                logger.error(f"Error in getting response: {e}")
                raise HTTPException(status_code=500, detail=f"Error in getting response: {e}")
    except WebSocketDisconnect:
        logger.info("Client disconnected")
    finally:
        await websocket.close()

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)