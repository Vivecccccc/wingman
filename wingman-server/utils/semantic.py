import torch
import numpy as np
from transformers import AutoTokenizer, AutoModel
from sklearn.metrics.pairwise import cosine_similarity

from constants import SEMANTIC_PARAMS

model_name, max_length, device = SEMANTIC_PARAMS['model_name'], SEMANTIC_PARAMS['max_length'], SEMANTIC_PARAMS['device']

tokenizer = AutoTokenizer.from_pretrained(model_name)
model = AutoModel.from_pretrained(model_name).to(device)

def get_semantic_embedding(text):
    inputs = tokenizer(text, return_tensors="pt", padding=True, truncation=True, max_length=max_length).to(device)
    if device == "cuda":
        inputs = {name: tensor.to(device) for name, tensor in inputs.items()}
    with torch.no_grad():
        outputs = model(**inputs)
    if device == "cuda":
        outputs = {name: tensor.cpu() for name, tensor in outputs.items()}
    return outputs["last_hidden_state"].mean(dim=1).squeeze().numpy()

def get_sim_vec(query: str, lib: np.ndarray):
    query_embedding = get_semantic_embedding(query).reshape(1, -1)
    sim_vec = cosine_similarity(query_embedding, lib)
    return sim_vec
