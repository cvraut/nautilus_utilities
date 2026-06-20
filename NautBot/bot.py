import discord
from discord.ext import commands
import aiohttp
import json
import os
from dotenv import load_dotenv
import asyncio
import logging

# Load environment variables
load_dotenv()

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

max_tokens = 131072

class OllamaBot(commands.Bot):
    def __init__(self):
        # Configure bot intents
        intents = discord.Intents.default()
        intents.message_content = True
        
        super().__init__(
            command_prefix='!',
            intents=intents,
            help_command=None
        )
        
        # Ollama configuration
        self.ollama_url = os.getenv('OLLAMA_BASE_URL', 'http://localhost:11434')
        self.default_model = os.getenv('DEFAULT_MODEL', 'llama3.1')
        
        # Track conversation context per channel
        self.conversations = {}
    
    async def setup_hook(self):
        """Initialize bot components"""
        logger.info("Bot is starting up...")
        await self.check_ollama_connection()
    
    async def check_ollama_connection(self):
        """Verify Ollama service is running"""
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(f"{self.ollama_url}/api/tags") as response:
                    if response.status == 200:
                        models = await response.json()
                        logger.info(f"Connected to Ollama. Available models: {len(models.get('models', []))}")
                    else:
                        logger.error("Failed to connect to Ollama service")
        except Exception as e:
            logger.error(f"Ollama connection error: {e}")
    
    async def generate_response(self, prompt, model=None, context=None):
        """Generate AI response using Ollama"""
        if not model:
            model = self.default_model
        
        # Prepare request payload
        payload = {
            "model": model,
            "prompt": prompt,
            "stream": False,
            "options": {
                "temperature": 0.7,
                "top_p": 0.9,
                "max_tokens": 500
            }
        }
        
        # Add conversation context if available
        if context:
            payload["context"] = context
        
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    f"{self.ollama_url}/api/generate",
                    json=payload,
                    timeout=aiohttp.ClientTimeout(total=60)
                ) as response:
                    
                    if response.status == 200:
                        result = await response.json()
                        return result.get('response', ''), result.get('context', [])
                    else:
                        error_text = await response.text()
                        logger.error(f"Ollama API error: {error_text}")
                        return "Sorry, I encountered an error processing your request.", []
                        
        except asyncio.TimeoutError:
            return "Request timed out. Please try again with a shorter message.", []
        except Exception as e:
            logger.error(f"Error generating response: {e}")
            return "I'm having trouble connecting to the AI service.", []

    async def safe_generate_response(self, prompt, retries=3):
        """Generate response with automatic retries"""
        for attempt in range(retries):
            try:
                response = await self.generate_response(prompt)
                return response
            except Exception as e:
                if attempt == retries - 1:
                    return "I'm experiencing technical difficulties. Please try again later."
                await asyncio.sleep(2 ** attempt)  # Exponential backoff

# Initialize bot instance
bot = OllamaBot()

@bot.event
async def on_ready():
    """Bot startup confirmation"""
    logger.info(f'{bot.user} has connected to Discord!')
    logger.info(f'Bot is in {len(bot.guilds)} servers')
    
    # Set bot status
    await bot.change_presence(
        activity=discord.Activity(
            type=discord.ActivityType.listening,
            name="your questions | !help"
        )
    )

@bot.event
async def on_message(message):
    if message.author.bot:
        return

    # Let discord.py check for commands first
    ctx = await bot.get_context(message)

    if ctx.valid:
        await bot.invoke(ctx)
        return

    # Normal DM message -> Ollama
    if isinstance(message.channel, discord.DMChannel):
        async with message.channel.typing():

            channel_id = message.channel.id
            context = bot.conversations.get(channel_id, [])

            response, new_context = await bot.generate_response(
                message.content,
                context=context
            )

            bot.conversations[channel_id] = new_context

            await message.reply(response)

        return

    # Server mention -> Ollama
    if bot.user in message.mentions:
        async with message.channel.typing():

            channel_id = message.channel.id
            context = bot.conversations.get(channel_id, [])

            clean_content = message.clean_content.replace(
                f"@{bot.user.display_name}", ""
            ).strip()

            response, new_context = await bot.generate_response(
                clean_content,
                context=context
            )

            bot.conversations[channel_id] = new_context

            await message.reply(response)

@bot.command(name='chat')
async def chat_command(ctx, *, prompt):
    """Direct chat command with Ollama"""
    async with ctx.typing():
        response, context = await bot.generate_response(prompt)
        
        # Store context for follow-up questions
        bot.conversations[ctx.channel.id] = context
        
        await ctx.reply(response)

@bot.command(name='model')
async def change_model(ctx, model_name=None):
    """Change the active AI model"""
    if not model_name:
        await ctx.reply(f"Current model: `{bot.default_model}`\nUse `!model <name>` to change.")
        return
    
    # Verify model exists
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(f"{bot.ollama_url}/api/tags") as response:
                if response.status == 200:
                    models_data = await response.json()
                    available_models = [m['name'] for m in models_data.get('models', [])]
                    
                    if model_name in available_models:
                        bot.default_model = model_name
                        await ctx.reply(f"✅ Changed to model: `{model_name}`")
                    else:
                        model_list = "\n".join([f"• {m}" for m in available_models])
                        await ctx.reply(f"❌ Model not found. Available models:\n```\n{model_list}\n```")
                else:
                    await ctx.reply("Error connecting to Ollama service.")
    except Exception as e:
        logger.error(f"Error changing model: {e}")
        await ctx.reply("Failed to change model. Check Ollama connection.")

@bot.command(name='clear')
async def clear_context(ctx):
    """Clear conversation context for this channel"""
    channel_id = ctx.channel.id
    if channel_id in bot.conversations:
        del bot.conversations[channel_id]
        await ctx.reply("🧹 Conversation context cleared!")
    else:
        await ctx.reply("No conversation context to clear.")

@bot.command(name='help')
async def help_command(ctx):
    """Display bot help information"""
    embed = discord.Embed(
        title="🤖 Ollama Discord Bot Help",
        description="AI-powered chat using local Ollama models",
        color=0x7289DA
    )
    
    embed.add_field(
        name="💬 Chat Methods",
        value="• Mention me: `@OllamaBot your question`\n• Direct command: `!chat your message`",
        inline=False
    )
    
    embed.add_field(
        name="⚙️ Commands",
        value="`!model` - View/change AI model\n`!clear` - Reset conversation\n`!help` - Show this help",
        inline=False
    )
    
    embed.add_field(
        name="🔧 Features",
        value="• Conversation memory per channel\n• Multiple AI model support\n• Local processing (privacy-first)",
        inline=False
    )
    
    embed.set_footer(text="Powered by Ollama | github.com/your-repo")
    
    await ctx.reply(embed=embed)

@bot.command(name="context")
async def context_size(ctx):
    """Show current conversation context size"""
    channel_id = ctx.channel.id

    context = bot.conversations.get(channel_id, [])

    max_tokens = 131072

    used_tokens = len(context)
    percent = (used_tokens / max_tokens) * 100

    await ctx.reply(
        f"📊 Context: {used_tokens}/{max_tokens} tokens ({percent:.2f}%)"
    )

# Error handling
@bot.event
async def on_command_error(ctx, error):
    """Handle command errors gracefully"""
    if isinstance(error, commands.CommandNotFound):
        return  # Ignore unknown commands
    elif isinstance(error, commands.MissingRequiredArgument):
        await ctx.reply("❌ Missing required argument. Use `!help` for command usage.")
    else:
        logger.error(f"Command error: {error}")
        await ctx.reply("❌ An error occurred processing your command.")

if __name__ == "__main__":
    # Start the bot
    token = os.getenv('DISCORD_TOKEN')
    if not token:
        logger.error("DISCORD_TOKEN not found in environment variables")
        exit(1)
    
    try:
        bot.run(token)
    except discord.LoginFailure:
        logger.error("Invalid Discord token")
    except Exception as e:
        logger.error(f"Bot startup error: {e}")