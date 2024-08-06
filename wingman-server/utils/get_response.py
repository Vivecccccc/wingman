import time
import json
import openai
import random
import logging

from enum import Enum
from typing import Optional, Dict, List
from json import JSONDecodeError
from pydantic import ValidationError
from fastapi import HTTPException

from constants import LLM_PARAMS
from utils.models import StageIntentDetectionResponse, StageInstructionGenerationResponse

logger = logging.getLogger(__name__)

default_vendor = LLM_PARAMS.get('default_vendor', 'alibaba-intl')
stage_0_prompt = LLM_PARAMS.get('initial_prompt', None)
stage_1_prompt = LLM_PARAMS.get('final_prompt', None)

class Stage(Enum):
    INTENT_DETECTION = 0
    INSTRUCTION_GENERATION = 1

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

async def get_response(stage: Stage, query: str, user_current_page_name: str, context_data: str, user_target_page_name: Optional[str] = None, vendor: str = default_vendor):
    sys_prompt = stage_0_prompt if stage == Stage.INTENT_DETECTION else stage_1_prompt
    if stage == Stage.INSTRUCTION_GENERATION:
        if user_target_page_name is None:
            raise ValueError("User target page name must be provided for instruction generation stage")
    user_prompt = f"""
    User Query: {query}
    User Current Page: {user_current_page_name}
    Context Data: {context_data}
    """ if Stage.INTENT_DETECTION else f"""
    User Query: {query}
    User Current Page: {user_current_page_name}
    User Final Target Page: {user_target_page_name}
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
                if stage == Stage.INTENT_DETECTION:
                    llm_response = StageIntentDetectionResponse(**message_content)
                else:
                    llm_response = StageInstructionGenerationResponse(**message_content)
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