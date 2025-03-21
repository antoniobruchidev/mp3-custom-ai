import json
import os
from dotenv import load_dotenv
import requests

load_dotenv()

registered_users_model = os.getenv("QWUEN_MODEL")
unregistered_users_model = os.getenv("MODEL")

# http://localhost:11434/v1/chat/completions in development
url = os.getenv("OPENAI_COMPATIBLE_SERVER")

# setting a base system prompt to format later
system_prompt = """
{prompt}

Below there is a list of character traits with assigned
a number and the reason why. The number will be on a scale between 1 and 10 where 1 is the 
minimum and 10 is the maximum.
You MUST answer accordingly to your character traits.
You MUST NOT share your character traits scores with the user.
You MUST NOT share your logic.

{traits}
"""


def chat(
    prompt=None,
    message=None,
    traits="",
    chat_history=None,
    question=None,
    collection_id=None,
):
    """Method to create

    Args:
        prompt (_type_): _description_
        traits (_type_): _description_
        message (_type_): _description_
    """
    response = None
    if question is not None and collection_id is not None:
        payload = {"question": question, "collection_id": collection_id}
        retriever_url = f"{url.split('11434')[0]}5001/query"
        response = requests.request("POST", retriever_url, json=payload)
        data = response.json()
        if data["status"] == 200:
            return {"status": 200, "message": data["message"]}
        else:
            return {"status": 400, "error": data["error"]}
    if chat_history is not None:
        payload = {"chat_history": json.dumps(chat_history)}
        ollama_url = f"{url.split('11434')[0]}5001/chat_with_history"
        response = requests.request("POST", ollama_url, json=payload)
        if response.status_code == 200:
            data = response.json()
            return data["message"], data["prompt_tokens"], data["comp_tokens"]
    if prompt is not None and traits != "":
        messages = [
            {
                "role": "system",
                "content": system_prompt.format(prompt=prompt, traits=traits),
            },
            {"role": "user", "content": message},
        ]
    elif prompt is not None and traits == "":
        messages = [
            {"role": "system", "content": prompt},
            {"role": "user", "content": message},
        ]
    payload = {
        "model": unregistered_users_model,
        "messages": messages,
        "max_tokens": 131000,
        "temperature": 1,
        "top_p": 0.8,
        "stream": False,
    }

    response = requests.request("POST", url, json=payload)
    ai_message = response.json()
    prompt_tokens = ai_message["usage"]["prompt_tokens"]
    completion_tokens = ai_message["usage"]["completion_tokens"]
    answer = ai_message["choices"][0]["message"]["content"]

    return answer, prompt_tokens, completion_tokens
