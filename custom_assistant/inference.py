import os
from dotenv import load_dotenv
import requests
load_dotenv()

registered_users_model = os.getenv("ABLITERATED_MODEL")
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



def chat(prompt, message, traits=None, chat_history=None):
    """Method to create

    Args:
        prompt (_type_): _description_
        traits (_type_): _description_
        message (_type_): _description_
    """
    if chat_history is None:
        if prompt is not None and traits is not None:
            messages = [
                {
                "role": "system", "content": system_prompt.format(
                        prompt=prompt,
                        traits=traits
                    )
                },
                {
                "role": "user", "content": message
                },
            ]
        elif prompt is not None and traits is None:
            message = [
                {"role": "system", "content": prompt},
                {"role": "user", "content": message},
            ]
    else:
        messages = chat_history
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
    print(ai_message)
    prompt_tokens = ai_message['usage']['prompt_tokens']
    completion_tokens = ai_message['usage']['completion_tokens']
    answer = ai_message['choices'][0]['message']['content']
    return answer, prompt_tokens, completion_tokens