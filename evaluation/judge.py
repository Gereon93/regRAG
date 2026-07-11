from deepeval.models import DeepEvalBaseLLM
from langchain_openai import ChatOpenAI

import config


class LokalerJudge(DeepEvalBaseLLM):
    def __init__(self):
        self._model = ChatOpenAI(
            model=config.LLM_MODELL, base_url=config.LLM_BASE_URL,
            api_key=config.LLM_API_KEY, temperature=0, timeout=config.LLM_TIMEOUT,
        )

    def load_model(self):
        return self._model

    def generate(self, prompt: str) -> str:
        return self._model.invoke(prompt).content

    async def a_generate(self, prompt: str) -> str:
        return (await self._model.ainvoke(prompt)).content

    def get_model_name(self) -> str:
        return f"lokal:{config.LLM_MODELL}"
