import os.path
from datetime import datetime
import json
from transformers import BertTokenizer, BertConfig, LlamaTokenizer, LlamaForCausalLM
from peft import LoraConfig
import torch
import argparse
from torch.utils.data import DataLoader
from dataset.LawClassifyDataSet import LawClassifyDataSet, LawClassify4BaseLine
from LawKeywordBert.models.bert_for_ner import BertSpanForNer
import pandas as pd
from typing import List, Dict
from utils import  get_metrics, get_id2label, labelmap


def get_parse():
    parser = argparse.ArgumentParser('Classify')
    parser.add_argument('--model_path', type=str, default="models/chinese_alpaca_2_1.3b_rlhf",
                        help='The model file path.')
    parser.add_argument('--model_type', type=str, default="llama",
                        help='The model file path.')
    parser.add_argument('--quantization', type=str, default='',
                        help='Load the model using quantization')
    parser.add_argument('--checkpoint_path', type=str,
                        default="checkpoint/chinese_alpaca_2_1.3b_rlhf_epochs1_lr0.001_examples600_2024-05-08_09-33-00",
                        help='The checkpoint file path.')
    parser.add_argument('--bert_model_path', type=str, default="bert_ner/keyword2.6_outputsbert",
                        help='The bert model file path.')
    parser.add_argument('--cls_data_dir', type=str, default="data",
                        help='The classify data directory.')
    parser.add_argument('--custom_data', type=str, default='data/extra_data/cail2018.json',
                        help='The learning rate.')
    parser.add_argument('--charge_list', type=list,
                        default=['危险驾驶', '盗窃', '故意伤害', '交通肇事', '走私、贩卖、运输、制造毒品'],
                        help='The charge list of classify.')
    parser.add_argument('--precedent_path', type=str, default='data/precedent_data.json',
                        help='The precedent file path.')
    parser.add_argument('--precedent_pt_path', type=str, default='data/keyword_precedent_db.pt',
                        help='The precedent file path.')
    parser.add_argument('--crime_law_path', type=str, default='data/crime_law.csv',
                        help='The crime law file path.')
    parser.add_argument('--llm_max_train_length', type=int, default=1024,
                        help='The maximum sequence length in train.')
    parser.add_argument('--llm_max_eval_length', type=int, default=1024,
                        help='The maximum sequence length in eval.')
    parser.add_argument("--bert_train_max_seq_length", default=512, type=int,
                        help="The maximum total input sequence length after tokenization. Sequences longer "
                             "than this will be truncated, sequences shorter will be padded.", )
    parser.add_argument("--bert_eval_max_seq_length", default=512, type=int,
                        help="The maximum total input sequence length after tokenization. Sequences longer "
                             "than this will be truncated, sequences shorter will be padded.", )
    parser.add_argument('--k_keywords', type=int, default=20,
                        help='The top k of keywords of sequence with similarity.')
    parser.add_argument('--n_keywords', type=int, default=10,
                        help='The n of keywords will be extraction.')
    parser.add_argument('--bert_ner_task_name', type=str, default='LKR',
                        help='The task name of bert ner.')
    parser.add_argument('--keyword_method', type=str, default='ner',
                        help='The method of LKR extraction.')
    parser.add_argument('--diverse_method', type=str, default='kmeans',
                        help='The method of LKR diverse process in LKR extraction.')
    parser.add_argument('--use_keywords', type=bool, default=True,
                        help='Using keywords for article retrieval.')
    parser.add_argument('--max_charge_length', type=int, default=5,
                        help='The max length of charge.')
    parser.add_argument('--max_output_length', type=int, default=11,
                        help='The max length of output tokens.')
    parser.add_argument('--baseline', type=bool, default=False,
                        help='The model file path.')
    parser.add_argument('--epochs', type=int, default=1,
                        help='The epochs.')
    parser.add_argument('--batch_size', type=int, default=2,
                        help='The batch size.')
    parser.add_argument('--val_batch_size', type=int, default=4,
                        help='The batch size.')
    parser.add_argument('--num_examples', type=int, default=600,
                        help='The batch size.')
    parser.add_argument('--val_num_examples', type=int, default=100,
                        help='The batch size.')
    parser.add_argument('--test_num_examples', type=int, default=300,
                        help='The batch size.')
    parser.add_argument('--learning_rate', type=float, default=1e-3,
                        help='The learning rate.')

    return parser


def init_lora(

):
    peft_config = LoraConfig(
        lora_alpha=16,
        lora_dropout=0.1,
        r=64,
        bias="none",
        task_type="CAUSAL_LM",
    )

    return peft_config


def load_model(
        model_path: str = 'chinese_alpaca_2_1.3b_rlhf',
        use_train: bool = True,
        model_type: str = 'llama',
):
    print('{} model is used.'.format(model_type))
    if model_type == 'llama':
        model = LlamaForCausalLM.from_pretrained(
            model_path,
            do_sample=False,
        ).to(device)

    else:
        raise ValueError('The model type {} is not supported.'.format(model_type))

    if use_train:
        lora = init_lora()
        model.add_adapter(lora)

    return model


def load_tokenizer(
        tokenizer_path: str = 'chinese_alpaca_2_1.3b_rlhf',
        use_train: bool = True,
        model_type: str = 'llama',
):
    if use_train:
        if model_type == 'llama':
            tokenizer = LlamaTokenizer.from_pretrained(tokenizer_path)
        else:
            raise ValueError('The {} tokenizer type is not supported.'.format(model_type))

    else:
        if model_type == 'llama':
            tokenizer = LlamaTokenizer.from_pretrained(tokenizer_path, padding_side='left')
        else:
            raise ValueError('The {} tokenizer type is not supported.'.format(model_type))

    if tokenizer.pad_token is None:
        tokenizer.add_special_tokens({'pad_token': '[PAD]'})

    return tokenizer


def load_data(
        batch_size: int = 2,
        data_type: str = 'train',
        data_dir: str = 'data',
        charge_list: list = None,
        precedent_db: List[Dict] = None,
        precedent_pt: torch.Tensor = None,
        crime_law: pd.DataFrame = None,
        k: int = 20,
        n: int = 10,
        task_name: str = 'LKR',
        bert_train_max_seq_length: int = 512,
        bert_eval_max_seq_length: int = 512,
        max_length: int = 1024,
        llm_tokenizer: LlamaTokenizer = None,
        max_charge_length: int = 10,
        num_examples: int = 2000,
        custom_data: str = '',
        keyword_method: str = 'ner',
        diverse_method: str = 'kmeans',
        use_keywords: bool = True,
):
    if charge_list is None:
        charge_list = ['危险驾驶', '盗窃', '故意伤害', '交通肇事', '走私、贩卖、运输、制造毒品']

    if not args.baseline:
        dataset = LawClassifyDataSet(
            data_type,
            data_dir,
            charge_list,
            bert_model,
            bert_tokenizer,
            bert_config,
            precedent_db=precedent_db,
            precedent_pt=precedent_pt,
            crime_law=crime_law,
            k=k,
            n=n,
            task_name=task_name,
            bert_train_max_seq_length=bert_train_max_seq_length,
            bert_eval_max_seq_length=bert_eval_max_seq_length,
            max_length=max_length,
            llm_tokenizer=llm_tokenizer,
            max_charge_length=max_charge_length,
            num_examples=num_examples,
            custom_data=custom_data,
            keyword_method=keyword_method,
            diverse_method=diverse_method,
            use_keywords=use_keywords,
        )

    else:
        dataset = LawClassify4BaseLine(
            data_type,
            data_dir,
            charge_list,
            max_length=max_length,
            llm_tokenizer=llm_tokenizer,
            max_charge_length=max_charge_length,
            num_examples=num_examples,
            custom_data=custom_data,
            model_type=args.model_path.split('/')[-1]
        )

    dataloader = DataLoader(dataset, shuffle=True, batch_size=batch_size)

    return dataloader


def train(
        model: LlamaForCausalLM,
        train_data: DataLoader,
        epochs: int = 10,
        learning_rate: float = 1e-3,
        precedent_db: List[Dict] = None,
        precedent_pt: torch.Tensor = None,
        crime_law: pd.DataFrame = None,
):
    optimizer = torch.optim.Adam(model.parameters(), lr=learning_rate)

    model.train()

    total_steps = len(train_data)
    log_loss = 0

    for epoch in range(epochs):
        for i, batch in enumerate(train_data):
            inputs = [input_text for input_text in batch["inputs"]]
            inputs = train_tokenizer(inputs,
                                    return_tensors="pt",
                                    padding='max_length',
                                    max_length=args.llm_max_train_length,
                                    truncation=True)
            input_ids = inputs.input_ids.to(device)
            attention_mask = inputs.attention_mask.to(device)
            outputs = model(input_ids=input_ids,
                            attention_mask=attention_mask,
                            labels=input_ids)

            loss = outputs.loss
            log_loss += loss.item()

            optimizer.zero_grad()
            loss.backward()
            optimizer.step()

            if (i + 1) % 100 == 0:
                print('Epoch:{}/{}, Batch:{}/{}, Loss: {:.4f}'.format(epoch + 1, epochs, i + 1, total_steps,
                                                                      log_loss / 100))
                log_loss = 0

        inference(
            model,
            data_type='val',
            precedent_db=precedent_db,
            precedent_pt=precedent_pt,
            crime_law=crime_law,
            max_output_length=args.max_output_length,
            num_examples=args.val_num_examples
        )

        model.train()


def inference(
        model,
        data_type: str = 'val',
        lora_path: str = '',
        precedent_db: List[Dict] = None,
        precedent_pt: torch.Tensor = None,
        crime_law: pd.DataFrame = None,
        max_output_length: int = 13,
        num_examples: int = 100,
        custom_data: str = '',
        result_path: str = '',
):
    val_tokenizer = load_tokenizer(args.model_path, use_train=False, model_type=args.model_type)

    val_data = load_data(
        args.val_batch_size,
        data_type,
        args.cls_data_dir,
        args.charge_list,
        precedent_db=precedent_db,
        precedent_pt=precedent_pt,
        crime_law=crime_law,
        k=args.k_keywords,
        n=args.n_keywords,
        task_name=args.bert_ner_task_name,
        bert_train_max_seq_length=args.bert_train_max_seq_length,
        bert_eval_max_seq_length=args.bert_eval_max_seq_length,
        max_length=args.llm_max_eval_length,
        llm_tokenizer=val_tokenizer,
        max_charge_length=args.max_charge_length,
        num_examples=num_examples,
        custom_data=custom_data,
        keyword_method=args.keyword_method,
        diverse_method=args.diverse_method,
        use_keywords=args.use_keywords,
    )

    if lora_path:
        model = load_model(args.model_path, use_train=False, model_type=args.model_type)
        model.load_adapter(lora_path)

    model.to(device)

    model.eval()

    # num_effect = 0
    total_val_data = len(val_data)

    print('{} data will be inferred. Size is {}.'.format(total_val_data * args.val_batch_size,
                                                         total_val_data))

    y_pred = []
    y_true = []

    res = pd.DataFrame(columns=['fact', 'charge', 'prediction'])

    for i, sample in enumerate(val_data):
        if (i + 1) % 100 == 0:
            print('{}/{}'.format(i + 1, total_val_data))

        # batch size > 1
        inputs = [input_text for input_text in sample["inputs"]]

        input_ids = val_tokenizer(inputs,
                                  return_tensors="pt",
                                  padding='max_length',
                                  max_length=args.llm_max_eval_length,
                                  truncation=True).input_ids.to(device)
        outputs = model.generate(input_ids, max_new_tokens=max_output_length, do_sample=False, repetition_penalty=1.1)
        output_texts = val_tokenizer.batch_decode(outputs, skip_special_tokens=True)
        pred_id = []
        for output_text in output_texts:
            if i < 1:
                print(output_text)
            output_text = output_text.replace('：', ':')
            output_text = output_text.replace(' ', '')
            output_text = output_text.split('问题:被告将以什么罪进行判决')[-1].strip()
            # print(output_text)
            pred_id.append(labelmap(output_text, args.charge_list))
        y_pred.extend(pred_id)
        y_true.extend(sample["charge_id"].detach().cpu().numpy().tolist())

    print('*' * 10, 'Classification Report', '*' * 10)
    get_metrics(y_true, y_pred, args.charge_list)
    print('*' * 50)

    if result_path:
        res.to_csv(result_path, index=False)


def prepare_precedent():
    # prepare precedent_data
    with open(args.precedent_path, 'r', encoding='utf-8') as f:
        precedent_db = json.load(f)
    precedent_pt = torch.load(args.precedent_pt_path).to(device)

    # prepare crime_law
    crime_law = pd.read_csv(args.crime_law_path)

    return precedent_db, precedent_pt, crime_law


def trainer(
        tokenizer,
        model,
        precedent_db,
        precedent_pt,
        crime_law,
):
    train_data = load_data(
        args.batch_size,
        'train',
        args.cls_data_dir,
        args.charge_list,
        precedent_db=precedent_db,
        precedent_pt=precedent_pt,
        crime_law=crime_law,
        k=args.k_keywords,
        n=args.n_keywords,
        task_name=args.bert_ner_task_name,
        bert_train_max_seq_length=args.bert_train_max_seq_length,
        bert_eval_max_seq_length=args.bert_eval_max_seq_length,
        max_length=args.llm_max_train_length,
        llm_tokenizer=tokenizer,
        max_charge_length=args.max_charge_length,
        num_examples=args.num_examples,
        keyword_method=args.keyword_method,
        diverse_method=args.diverse_method,
        use_keywords=args.use_keywords,
    )

    train(model, train_data, args.epochs, args.learning_rate, precedent_db, precedent_pt, crime_law)


if __name__ == '__main__':
    args = get_parse().parse_args()
    device = torch.device('cuda') if torch.cuda.is_available() else torch.device('cpu')

    bert_tokenizer = BertTokenizer.from_pretrained(args.bert_model_path)
    bert_config = BertConfig.from_pretrained(args.bert_model_path)
    bert_model = BertSpanForNer.from_pretrained(args.bert_model_path, config=bert_config).to(device)

    precedent_db, precedent_pt, crime_law = prepare_precedent()

    model = load_model(args.model_path, use_train=True, model_type=args.model_type).to(device)
    train_tokenizer = load_tokenizer(args.model_path, use_train=True, model_type=args.model_type)

    trainer(train_tokenizer, model, precedent_db, precedent_pt, crime_law)

    # save model
    if not os.path.exists('checkpoint'):
        os.mkdir('checkpoint')

    timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    save_path = (f"checkpoint/chatglm_{args.model_path.split('/')[-1]}_epochs{args.epochs}_lr{args.learning_rate}_"
                 f"examples{args.num_examples}_{timestamp}")

    model.save_pretrained(save_path)
    train_tokenizer.save_pretrained(save_path)

    print('*' * 30, 'Test Metrics', '*' * 30)
    inference(
        model,
        'test',
        # lora_path=args.checkpoint_path,
        max_output_length=args.max_output_length,
        precedent_db=precedent_db,
        precedent_pt=precedent_pt,
        crime_law=crime_law,
        num_examples=args.test_num_examples,
        # custom_data=args.custom_data,
        # result_path='',
    )
