"""
---
title: Finetune GPT-2 with LoRA
summary: This is training code with notes for fine-tuning pre-trained GPT-2 model with LoRA.
---

# Finetune GPT-2 with [LoRA](index.html)

Here's a Colab notebook for training a feedback transformer on Tiny Shakespeare dataset.

[![Open In Colab](https://colab.research.google.com/assets/colab-badge.svg)](https://colab.research.google.com/github/labmlai/annotated_deep_learning_paper_implementations/blob/master/labml_nn/lora/experiment.ipynb)
"""

import torch
from labml import lab, monit, tracker
from labml.configs import BaseConfigs, option
from labml.utils.download import download_file
from labml_helpers.device import DeviceConfigs
from torch.optim import Adam
from torch.utils.data import DataLoader, TensorDataset
from transformers import AutoTokenizer, AutoModelForCausalLM
from labml_nn.lora.gpt2 import GPTModel


class Trainer(BaseConfigs):
    """
    ## Trainer configurations and the training loop

    The default configs can and will be over-ridden when we start the experiment
    """
    device: torch.device = DeviceConfigs()

    # GPT-2 configs
    layer_norm_epsilon: float = 1e-05
    n_embed: int = 768
    n_layer: int = 12
    n_positions: int = 1024
    vocab_size: int = 50257

    # Training configs
    epochs: int = 10
    batch_size: int = 32
    learning_rate: float = 1e-4
    context_len: int = 512

    # LoRA rank
    lora_r: int = 32

    # Dataset
    text: TensorDataset = "tiny_shakespeare"
    tokenizer = AutoTokenizer.from_pretrained("gpt2")
    model: GPTModel
    optimizer: torch.optim.Adam
    criterion = torch.nn.CrossEntropyLoss()
    data_loader: DataLoader

    def _load_pretrained_weights(self):
        """
        ### Load pre-trained [GPT-2 from huggingface](https://huggingface.co/openai-community/gpt2)
        """

        # Load the huggingface model and get the parameters
        hf_model = AutoModelForCausalLM.from_pretrained("gpt2")
        state_dict = hf_model.state_dict()

        # Transformer embedding and prediction layer parameter mapping (`hf: ours`)
        mapping = {
            'transformer.wte.weight': 'token_embedding.weight',
            'transformer.wpe.weight': 'position_embedding.weight',
            'transformer.ln_f.weight': 'final_norm.weight',
            'transformer.ln_f.bias': 'final_norm.bias',
            'lm_head.weight': 'lm_head.weight'
        }

        # Mapping (`hf: ours`) of decoder layers
        for i in range(12):
            mapping[f'transformer.h.{i}.ln_1.weight'] = f'blocks.{i}.pre_norm.weight'
            mapping[f'transformer.h.{i}.ln_1.bias'] = f'blocks.{i}.pre_norm.bias'
            mapping[f'transformer.h.{i}.attn.c_attn.weight'] = f'blocks.{i}.attn.qkv_projection.weight'
            mapping[f'transformer.h.{i}.attn.c_attn.bias'] = f'blocks.{i}.attn.qkv_projection.bias'
            mapping[f'transformer.h.{i}.attn.c_proj.weight'] = f'blocks.{i}.attn.output_projection.weight'
            mapping[f'transformer.h.{i}.attn.c_proj.bias'] = f'blocks.{i}.attn.output_projection.bias'
            mapping[f'transformer.h.{i}.ln_2.weight'] = f'blocks.{i}.post_norm.weight'
            mapping[f'transformer.h.{i}.ln_2.bias'] = f'blocks.{i}.post_norm.bias'
            mapping[f'transformer.h.{i}.mlp.c_fc.weight'] = f'blocks.{i}.ffn.linear_in.weight'
            mapping[f'transformer.h.{i}.mlp.c_fc.bias'] = f'blocks.{i}.ffn.linear_in.bias'
            mapping[f'transformer.h.{i}.mlp.c_proj.weight'] = f'blocks.{i}.ffn.linear_out.weight'
            mapping[f'transformer.h.{i}.mlp.c_proj.bias'] = f'blocks.{i}.ffn.linear_out.bias'

        # Move the parameters based on mapping
        new_state_dict = {}
        for old_key, new_key in mapping.items():
            if old_key in state_dict:
                new_state_dict[new_key] = state_dict[old_key]

        # GPT-2 hugging face uses 1D Convolution layers. We need to transpose those weights since we use linear layers
        convo_layers = ([f'blocks.{i}.ffn.linear_in.weight' for i in range(12)] +
                        [f'blocks.{i}.ffn.linear_out.weight' for i in range(12)] +
                        [f'blocks.{i}.attn.qkv_projection.weight' for i in range(12)] +
                        [f'blocks.{i}.attn.output_projection.weight' for i in range(12)])

        for layer in convo_layers:
            new_state_dict[layer] = torch.transpose(new_state_dict[layer], 0, 1)

        # Load out model
        self.model.load_state_dict(new_state_dict, strict=False)  # state dict does not have lora weights

    def initialize(self):
        """
        ### Initialize the model, optimizer and dataloader
        """
        # Initialize the model
        self.model = GPTModel(
            layer_norm_epsilon=self.layer_norm_epsilon,
            n_embd=self.n_embed,
            n_layer=self.n_layer,
            n_positions=self.n_positions,
            vocab_size=self.vocab_size,
            r=self.lora_r,
        )
        self.model.to(self.device)
        # Load pre-trained model weights
        self._load_pretrained_weights()

        # Initialize the optimizer
        self.optimizer = Adam(self.model.parameters(), lr=self.learning_rate)

        # Initialize the data loader
        self.data_loader = DataLoader(self.text, batch_size=self.batch_size, shuffle=True)

    def run(self):
        """
        ### Training loop
        """

        for _ in monit.loop(self.epochs):
            for (inputs, ) in monit.iterate('Train', self.data_loader):
                inputs = inputs.to(self.device)
                labels = inputs.clone()

                outputs = self.model(inputs)

                shift_logits = outputs[..., :-1, :]
                shift_labels = labels[..., 1:]

                loss = self.criterion(shift_logits.reshape(-1, shift_logits.size(-1)), shift_labels.reshape(-1))

                self.optimizer.zero_grad()
                loss.backward()
                self.optimizer.step()

                tracker.add({'loss': loss})

                tracker.save()
                tracker.add_global_step()
            tracker.new_line()


@option(Trainer.text)
def tiny_shakespeare(c: Trainer):
    """
    ### Tiny Shakespeare dataset

    It will download from the url if not present
    """
    path = lab.get_data_path() / 'tiny_shakespeare.txt'
    if not path.exists():
        download_file("https://raw.githubusercontent.com/karpathy/char-rnn/master/data/tinyshakespeare/input.txt", path)
    with open(path, 'r', encoding='utf-8') as f:
        text = f.read()

    tokens = c.tokenizer.encode(text)
    num_batches = len(tokens) // (c.batch_size * c.context_len)
    tokens = tokens[:num_batches * c.batch_size * c.context_len]
    input_ids = torch.tensor(tokens).view(-1, c.context_len)
    return TensorDataset(input_ids)