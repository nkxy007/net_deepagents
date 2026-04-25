from openai import OpenAI
import os
import base64
from typing import ByteString
from anthropic import Anthropic
import google.generativeai as genai
from google.generativeai import types
import requests

import json
import logging

logger = logging.getLogger(__name__)

def get_models_config():
    import os
    home_config = os.path.expanduser("~/.net-deepagent/models.json")
    if os.path.exists(home_config):
        try:
            with open(home_config, "r") as f:
                return json.load(f)
        except Exception as e:
            logger.error(f"Error reading {home_config}: {e}")
            
    project_config = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "config", "models.json")
    if os.path.exists(project_config):
        try:
            with open(project_config, "r") as f:
                return json.load(f)
        except Exception as e:
            logger.error(f"Error reading {project_config}: {e}")
            
    return {}

class AIHelper:
    def __init__(self, key: str, model="openai", intelligence=False) -> None:
        self.key = key
        self.model = model
        self.config = get_models_config()
        self.exact_model = None

        if self.model == "openai":
            self.client = OpenAI(api_key=self.key)
            self.exact_model = self.config.get("openai_image", "gpt-5-mini")
        elif self.model == "openai-big":
            self.client = OpenAI(api_key=self.key)
            self.exact_model = self.config.get("openai-big_image", "gpt-5")
        elif "claude" in self.model.lower():
            os.environ["ANTHROPIC_API_KEY"] = self.key
            self.client = Anthropic()
            self.exact_model = self.config.get("claude_image", "claude-sonnet-4-5-20250929") if self.model == "claude" else self.model
        elif "gemini" in self.model.lower():
            os.environ["GEMINI_API_KEY"] = self.key
            #self.client = genai.Client(api_key=self.key)
            self.exact_model = self.config.get("gemini_image", "gemini-3.0-flash") if self.model == "gemini" else self.model
        elif "grok" in self.model.lower():
            self.client = OpenAI(
                api_key=self.key,
                base_url="https://api.xai.com/v1"
            )
            self.exact_model = self.config.get("grok_image", "grok-4-fast-reasoning") if self.model == "grok" else self.model
        elif "gpt" in self.model.lower() or "openai" in self.model.lower():
            self.client = OpenAI(api_key=self.key)
            self.exact_model = self.model
        else:
            self.exact_model = self.model

        if intelligence:
            self.exact_model = self.config.get("openai-big_image", "gpt-5")

    def encode_image(self, image_path):
        with open(image_path, "rb") as image_file:
            return base64.b64encode(image_file.read()).decode('utf-8')
    
    def gemini_wrapper(self, image_data:str, context:str, image_type="png"):
        """This function takes an image path and generates interpretation using Google Gemini"""
        response = self.client.models.generate_content(
            model=getattr(self, 'exact_model', self.config.get("gemini_image", "gemini-3.0-flash")),
            contents=[context, 
            types.Part.from_bytes(data=image_data, mime_type=f"image/{image_type}")])
        return response
        
    def anthropic_wrapper(self, image_data:ByteString, context:str, image_type="png"):
        message = self.client.messages.create(
        model=getattr(self, 'exact_model', self.config.get("claude_image", "claude-sonnet-4-5-20250929")),
        max_tokens=8192,
        messages=[
            {
                "role": "user",
                "content": [
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": f"image/{image_type}",
                            "data": image_data,
                        },
                    },
                    {
                    "type": "text",
                    "text": context
                    }
                ],
            }
        ],
        )
        return message
    

    def get_xai_response(self, context, image_type, image_encoded, api_key, temperature=0.7):
        url = "https://api.x.ai/v1/chat/completions"
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}"
        }
        data = {
            "messages": [
                {
                  "role": "user",
                  "content": [
        
                    {
                      "type": "image_url",
                      "image_url": {
                        "url": f"data:image/{image_type};base64,{image_encoded}",
                        "detail": "high"
                      },
                    },
                    {"type": "text", "text": context}
                  ],
                }
              ],
            "model": getattr(self, 'exact_model', self.config.get("grok_image", "grok-4-fast-reasoning")),
            "stream": False,
            "temperature": temperature
        }
        try:
            response = requests.post(url, headers=headers, json=data)
            response.raise_for_status()  
            result = response.json()
            logger.debug(f"AI Response: {result}")
            return result['choices'][0]['message']['content']
            
        except requests.exceptions.RequestException as e:
            logger.error(f"An error occurred: {e}")
        except KeyError as e:
            logger.error(f"Missing environment variable: {e}")

    def get_image_analysis(self, image_encoded:ByteString, context: str, temperature=1, image_type="png") -> str:
        if "claude" in self.model.lower():
            logger.info("Using Claude model for image analysis")
            response = self.anthropic_wrapper(image_encoded, context, image_type)
            logger.debug(response)
            return response.content[0].text
        elif "gemini" in self.model.lower():
            logger.info("Using Gemini model for image analysis")
            response = self.gemini_wrapper(image_encoded, context, image_type)
            logger.debug(response)
            return response
        elif "grok" in self.model.lower():
            return self.get_xai_response(context, image_type, image_encoded, self.key, temperature)
        else:
            logger.info(f"Using model {self.exact_model} for image analysis")
            response = self.client.chat.completions.create(
              model= self.exact_model,
              messages=[
                {
                  "role": "user",
                  "content": [
                    {"type": "text", "text": context},
                    {
                      "type": "image_url",
                      "image_url": {
                        "url": f"data:image/{image_type};base64,{image_encoded}",
                        "detail": "high"
                      },
                    },
                  ],
                }
              ],
              max_completion_tokens=15000,
              temperature=temperature if not "gpt-5" in self.exact_model else 1
            )
            response_to_use = response.choices[0]
            logger.debug(f"AI Response: {response_to_use}")
            logger.debug(f"Entire AI response: {response}")
            return response_to_use.message.content
