from deepeval.models import DeepEvalBaseLLM
from langchain_openai import ChatOpenAI

import config


class LokalerJudge(DeepEvalBaseLLM):
    def __init__(self, modell=None):
        self._name = modell or config.JUDGE_MODELL
        self._model = ChatOpenAI(
            model=self._name, base_url=config.LLM_BASE_URL, api_key=config.LLM_API_KEY,
            temperature=0, timeout=config.LLM_TIMEOUT,
        )

    def load_model(self):
        return self._model

    def generate(self, prompt: str, schema=None):
        if schema is not None:
            return self._model.with_structured_output(schema).invoke(prompt)
        return self._model.invoke(prompt).content

    async def a_generate(self, prompt: str, schema=None):
        if schema is not None:
            return await self._model.with_structured_output(schema).ainvoke(prompt)
        return (await self._model.ainvoke(prompt)).content

    def get_model_name(self) -> str:
        return f"lokal:{self._name}"
