import config


def create_embed_model(device=None):
    from llama_index.embeddings.huggingface import HuggingFaceEmbedding

    return HuggingFaceEmbedding(model_name=config.EMBEDDING_MODELL, device=device)
