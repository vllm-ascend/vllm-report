## 暂未实现的功能
1. /home/wxy/code/vllm-report/src/data/analyze_commits.py支持断点续推理功能，比如40个commit，每轮15个总结完后保存一下，这样即使后续的轮次失败了，前面轮次的内容也保存了，再次执行的时候不用重复推理，节省token
2. 