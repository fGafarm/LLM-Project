@echo off
set CUDA_VISIBLE_DEVICES=0
set OLLAMA_MAX_LOADED_MODELS=1
set OLLAMA_NUM_PARALLEL=1
set OLLAMA_HOST=0.0.0.0:11434
set OLLAMA_DEBUG=INFO
"C:\Users\shun nabeno\AppData\Local\Programs\Ollama\ollama.exe" serve
