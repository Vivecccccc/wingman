import json
import time
import logging
import openai

from fastapi import FastAPI, HTTPException

from constants import GRAPH_PARAMS, SEMANTIC_PARAMS, LLM_PARAMS
from compute import read_graph, prepare_library, get_context
from utils.models import UserRequest, LlmResponse

logging.basicConfig(filename="main.log", level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

app = FastAPI()

sys_prompt = LLM_PARAMS['system_prompt']

try:
    g = read_graph(GRAPH_PARAMS['graph_path'])
    lib = prepare_library(g, SEMANTIC_PARAMS.get('lib_path', None))
except Exception as e:
    logger.error(f"Error in reading graph or preparing library: {e}")
    raise e

def get_response(query: str, user_current_page_name: str, vendor: str = "alibaba"):
    if vendor == "alibaba":
        vendor_config = LLM_PARAMS['config_alibaba']
    elif vendor == "deepseek":
        vendor_config = LLM_PARAMS['config_deepseek']
    else:
        raise NotImplementedError(f"Vendor {vendor} not implemented, please choose from 'alibaba' or 'deepseek'")
    api_key = vendor_config['api_key']
    endpoint = vendor_config['endpoint']
    model = vendor_config['model']
    try:
        context_data = get_context(g, lib, query, user_current_page_name)
    except Exception as e:
        logger.error(f"Error in generating context data: {e}")
        raise HTTPException(status_code=500, detail=f"Error in generating context data: {e}")

    user_prompt = f"""
    User Query: {query}
    User Current Page: {user_current_page_name}
    Context Data: {context_data}
    """
    start_time = time.time()
    try:
        client = openai.OpenAI(api_key=api_key, base_url=endpoint)
        response = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": sys_prompt},
                {"role": "user", "content": user_prompt},
            ],
            max_tokens=LLM_PARAMS.get('max_tokens', None),
            temperature=LLM_PARAMS.get('temperature', 0.0),
            top_p=LLM_PARAMS.get('top_p', None),
            stream=False,
            response_format={"type": "json_object"}
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error in sending request: {e}")
    # usage of tokens
    time_elapsed = time.time() - start_time
    logger.info(f"Request elapsed: {time_elapsed}")
    logger.info(f"Total tokens: {response.usage.total_tokens}; Input tokens: {response.usage.prompt_tokens}; Output tokens: {response.usage.completion_tokens}")
    return response.choices[0].message.content


@app.post("/navigate")
async def navigate(request: UserRequest):
    user_query = request.user_query
    page_history = request.page_history
    user_current_page_name = page_history[-1]

    if not user_query or not user_current_page_name:
        logger.error("User query and current page name must be provided")
        raise HTTPException(status_code=400, detail="User query and current page name must be provided")
    
    try:
        raw_response = get_response(user_query, user_current_page_name)
        response = LlmResponse(**(json.loads(raw_response)))
    except Exception as e:
        logger.error(f"Error in generating response: {e}")
        raise e
    return response.dict()

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)