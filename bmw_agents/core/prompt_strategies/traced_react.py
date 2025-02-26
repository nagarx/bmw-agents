"""
Traced ReAct prompt strategy for the BMW Agents framework.
This module implements a version of ReAct prompt strategy that explicitly tracks execution trace.
"""

import json
import re
from typing import Any, Dict, List, Optional, Tuple

from bmw_agents.core.prompt_strategies.base import PromptStrategy
from bmw_agents.core.prompt_strategies.react import ReActPromptStrategy, Tool
from bmw_agents.utils.llm_providers import LLMProvider
from bmw_agents.utils.logger import get_logger

logger = get_logger("prompt_strategies.traced_react")


class TracedReAct(PromptStrategy):
    """
    Implementation of the ReAct prompt strategy with explicit trace tracking.

    This strategy iteratively prompts the LLM to think, act, and observe,
    while explicitly tracking the execution trace.
    """

    def __init__(
        self,
        llm_provider: LLMProvider,
        tools: List[Any] = None,
        template_path: str = "react.txt",
        max_iterations: int = 10,
        termination_sequence: str = "FINAL ANSWER:",
    ) -> None:
        """
        Initialize a TracedReAct prompt strategy.

        Args:
            llm_provider: The LLM provider to use
            tools: List of tools available to the agent
            template_path: Path to the template file
            max_iterations: Maximum number of iterations
            termination_sequence: Sequence that marks the end of execution
        """
        super().__init__(llm_provider, template_path=template_path)
        self.tools = tools or []
        self.max_iterations = max_iterations
        self.termination_sequence = termination_sequence

        # Initialize trace arrays
        self.thoughts: List[str] = []
        self.actions: List[Dict[str, Any]] = []
        self.observations: List[str] = []

    def get_tool_descriptions(self) -> str:
        """
        Get a formatted string describing all available tools.

        Returns:
            Formatted tool descriptions
        """
        if not self.tools:
            return "No tools available."

        descriptions = []
        for i, tool in enumerate(self.tools):
            descriptions.append(f"{i+1}. {tool.name}: {tool.description}")

        return "\n".join(descriptions)

    def find_tool_by_name(self, name: str) -> Optional[Any]:
        """
        Find a tool by its name.

        Args:
            name: The name of the tool to find

        Returns:
            The tool object if found, None otherwise
        """
        name = name.lower().strip()
        for tool in self.tools:
            if tool.name.lower() == name:
                return tool
        return None

    async def run(self, instruction: str) -> Dict[str, Any]:
        """
        Run the TracedReAct prompt strategy.

        Args:
            instruction: The instruction/query to execute

        Returns:
            Dictionary with result and execution trace
        """
        # Reset trace arrays
        self.thoughts = []
        self.actions = []
        self.observations = []

        # Execute the strategy
        final_answer = await self.execute(instruction)

        # Return both the result and the trace
        return {
            "result": final_answer,
            "trace": {
                "thoughts": self.thoughts,
                "actions": self.actions,
                "observations": self.observations,
            },
        }

    async def execute(self, instruction: str, **kwargs: Any) -> str:
        """
        Execute the TracedReAct prompt strategy.

        Args:
            instruction: The instruction/query to process
            **kwargs: Additional variables for the template

        Returns:
            The final result after the ReAct loop
        """
        logger.info("Starting TracedReAct execution")
        # Start with a clean slate
        self.clear_messages()

        # Render the system message template
        variables = {
            "instruction": instruction,
            "tools": self.get_tool_descriptions(),
            "termination_sequence": self.termination_sequence,
            **kwargs,
        }
        system_content = self.render_template(**variables)

        # Add system message
        self.add_system_message(system_content)

        # Add the user instruction
        self.add_user_message(instruction)

        # Main ReAct loop
        iteration = 0
        final_answer = None

        while iteration < self.max_iterations:
            logger.info(f"Starting iteration {iteration+1}/{self.max_iterations}")

            # Get LLM response
            response = await self.get_llm_response(
                temperature=kwargs.get("temperature", 0.7),
                max_tokens=kwargs.get("max_tokens", None),
            )

            # Extract content
            content = response["content"]
            self.add_assistant_message(content)

            # Print debug info
            print(f"\nLLM Response (Iteration {iteration+1}):\n{content}\n")

            # Check for termination
            if self.termination_sequence in content:
                # Extract final answer
                final_answer = content.split(self.termination_sequence, 1)[1].strip()
                logger.info(
                    f"Termination sequence found, final answer: {final_answer[:100]}..."
                )
                break

            # Process the response to extract thought and action
            thought, action = self.extract_thought_and_action(content)

            # Debug extraction results
            print(
                f"Extracted Thought: {thought[:100]}..." if thought else "No thought extracted"
            )
            print(f"Extracted Action: {action}" if action else "No action extracted")

            # Always add thought to the trace
            if thought:
                self.thoughts.append(thought)
                print(f"Added thought to trace. Trace now has {len(self.thoughts)} thoughts")

            # If no action is found or action is invalid, ask for clarification
            if not action:
                logger.warning("No valid action found in response")
                self.add_user_message(
                    "I couldn't understand your action. Please try again with a valid action."
                )
                iteration += 1
                continue

            # Add action to trace
            self.actions.append(action)
            print(f"Added action to trace. Trace now has {len(self.actions)} actions")

            # Execute the action
            observation = await self.execute_action(action)

            # Add observation to trace
            self.observations.append(observation)
            print(
                f"Added observation to trace. Trace now has {len(self.observations)} observations"
            )

            # Add observation as user message
            self.add_user_message(f"Observation: {observation}")

            iteration += 1

        else:
            # If we reach max iterations without termination
            logger.warning(
                f"Reached maximum iterations ({self.max_iterations}) without termination"
            )
            final_answer = (
                "I wasn't able to complete the task within the allowed number of steps."
            )

        logger.info(
            f"Completed TracedReAct Execution with {len(self.thoughts)} thoughts, {len(self.actions)} actions, and {len(self.observations)} observations"
        )
        return final_answer or "No final answer was produced."

    def extract_thought_and_action(self, content: str) -> Tuple[str, Optional[Dict[str, Any]]]:
        """
        Extract thought and action from the LLM response.

        Args:
            content: The LLM response content

        Returns:
            Tuple of (thought, action) where action is a dictionary or None
        """
        # Remove <think>...</think> tags if present
        content = re.sub(r"<think>.*?</think>", "", content, flags=re.DOTALL)

        # Clean up any format markers or additional text before the expected format
        content = re.sub(r"^.*?(Thought:)", r"\1", content, flags=re.DOTALL, count=1)

        # Extract thought - be more flexible with the pattern
        # Handle formats like "Thought:", "**Thought:**", etc.
        thought_pattern = (
            r"\*{0,2}Thought:?\*{0,2}(.*?)(?:\*{0,2}Action:?\*{0,2}|\*{0,2}FINAL ANSWER:?\*{0,2}|$)"
        )
        thought_match = re.search(thought_pattern, content, re.DOTALL | re.IGNORECASE)
        thought = thought_match.group(1).strip() if thought_match else ""

        # If we found a FINAL ANSWER marker, there won't be an action
        if re.search(r"\*{0,2}FINAL ANSWER:?\*{0,2}", content, re.IGNORECASE):
            return thought, None

        # Extract action - be more flexible with the pattern
        # Handle formats like "Action:", "**Action:**", etc.
        action_pattern = r"\*{0,2}Action:?\*{0,2}(.*?)(?:\*{0,2}Observation:?\*{0,2}|$)"
        action_match = re.search(action_pattern, content, re.DOTALL | re.IGNORECASE)
        action_str = action_match.group(1).strip() if action_match else None

        # If no action found, return early
        if not action_str:
            return thought, None

        # Try to parse action as JSON
        try:
            # First, try to find a JSON object directly
            json_pattern = r"{[^{}]*}"
            json_matches = re.findall(json_pattern, action_str, re.DOTALL)

            if json_matches:
                for json_str in json_matches:
                    try:
                        action_json = json.loads(json_str)
                        # If we have a properly formatted action with a tool key, return it
                        if "tool" in action_json:
                            return thought, action_json
                    except json.JSONDecodeError:
                        continue

            # If no valid JSON object was found, try to clean up the string and parse again
            # Remove any markdown formatting, new lines, etc.
            cleaned_str = re.sub(r"[\s\n\r]+", " ", action_str)  # Normalize whitespace
            cleaned_str = re.sub(r'[*"`\']+', "", cleaned_str)  # Remove formatting chars

            json_pattern = r"{[^{}]*}"
            json_matches = re.findall(json_pattern, cleaned_str, re.DOTALL)

            if json_matches:
                for json_str in json_matches:
                    try:
                        action_json = json.loads(json_str)
                        if "tool" in action_json:
                            return thought, action_json
                    except json.JSONDecodeError:
                        continue

            # If we still don't have valid JSON, try a more lenient approach using regex
            tool_match = re.search(r"tool\s*:\s*['\"]?([^'\"\s]+)['\"]?", action_str, re.IGNORECASE)
            args_match = re.search(r"args\s*:\s*({.*})", action_str, re.DOTALL | re.IGNORECASE)

            if tool_match:
                tool_name = tool_match.group(1)
                args = {}

                if args_match:
                    try:
                        args = json.loads(args_match.group(1))
                    except json.JSONDecodeError:
                        # Still can't parse args, try key-value extraction
                        kv_matches = re.findall(
                            r"['\"]?(\w+)['\"]?\s*:\s*['\"]?([^'\",\s]+)['\"]?", args_match.group(1)
                        )
                        args = {k: v for k, v in kv_matches}

                return thought, {"tool": tool_name, "args": args}

        except Exception as e:
            logger.warning(f"Failed to parse action as JSON: {e}")

        return thought, None

    async def execute_action(self, action: Dict[str, Any]) -> str:
        """
        Execute the specified action using the appropriate tool.

        Args:
            action: The action to execute, with 'tool' and 'args' keys

        Returns:
            The result of the action execution
        """
        tool_name = action.get("tool")
        args = action.get("args", {})

        if not tool_name:
            return "Error: No tool specified in the action."

        tool = self.find_tool_by_name(tool_name)
        if not tool:
            return f"Error: Tool '{tool_name}' not found."

        try:
            logger.info(f"Executing tool: {tool_name}")
            result = await tool.execute(**args)
            return str(result)
        except Exception as e:
            error_msg = f"Error executing tool '{tool_name}': {str(e)}"
            logger.error(error_msg)
            return error_msg

    def post_process(self, response: Dict[str, Any]) -> str:
        """
        Process the response from the LLM.

        Args:
            response: The raw response from the LLM

        Returns:
            Processed content
        """
        return response["content"]
