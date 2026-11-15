"""create_embed_model in der CI: llama_index ist gestubbt, geprüft wird Modellname und Device."""

import sys
from types import ModuleType

import config
import embedding


class FakeHuggingFaceEmbedding:
    def __init__(self, model_name, device):
        self.model_name, self.device = model_name, device


def test_create_embed_model_nimmt_konfiguriertes_modell_und_device(monkeypatch):
    modul = ModuleType("llama_index.embeddings.huggingface")
    modul.HuggingFaceEmbedding = FakeHuggingFaceEmbedding
    for name in ("llama_index", "llama_index.embeddings"):
        monkeypatch.setitem(sys.modules, name, ModuleType(name))
    monkeypatch.setitem(sys.modules, "llama_index.embeddings.huggingface", modul)
    monkeypatch.setattr(config, "EMBEDDING_MODELL", "test/modell")

    modell = embedding.create_embed_model(device="mps")

    assert isinstance(modell, FakeHuggingFaceEmbedding)
    assert (modell.model_name, modell.device) == ("test/modell", "mps")
