### Copyright (c) 2018 Idiap Research Institute, http://www.idiap.ch/
### Modified by Lesly Miculicich <lmiculicich@idiap.ch>
# 
# This file is part of HAN-NMT.
# 
# HAN-NMT is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License version 3 as
# published by the Free Software Foundation.
# 
# HAN-NMT is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
# GNU General Public License for more details.
# 
# You should have received a copy of the GNU General Public License
# along with HAN-NMT. If not, see <http://www.gnu.org/licenses/>.

"""
This file is for models creation, which consults options
and creates each encoder and decoder accordingly.
"""
import torch
import torch.nn as nn

import onmt
import onmt.io
import onmt.Models
import onmt.modules
from onmt.Models import NMTModel, MeanEncoder, RNNEncoder, \
						StdRNNDecoder, InputFeedRNNDecoder
from onmt.modules import Embeddings, ImageEncoder, CopyGenerator, \
						 TransformerEncoder, TransformerDecoder, \
						 CNNEncoder, CNNDecoder, AudioEncoder,  \
						 HierarchicalContext
from onmt.Utils import use_gpu
from torch.nn.init import xavier_uniform


def make_embeddings(opt, word_dict, feature_dicts, for_encoder=True):
	"""
	Make an Embeddings instance.
	Args:
		opt: the option in current environment.
		word_dict(Vocab): words dictionary.
		feature_dicts([Vocab], optional): a list of feature dictionary.
		for_encoder(bool): make Embeddings for encoder or decoder?
	"""
	if for_encoder:
		embedding_dim = opt.src_word_vec_size
	else:
		embedding_dim = opt.tgt_word_vec_size

	word_padding_idx = word_dict.stoi[onmt.io.PAD_WORD]
	num_word_embeddings = len(word_dict)

	feats_padding_idx = [feat_dict.stoi[onmt.io.PAD_WORD]
						 for feat_dict in feature_dicts]
	num_feat_embeddings = [len(feat_dict) for feat_dict in
						   feature_dicts]

	return Embeddings(word_vec_size=embedding_dim,
					  position_encoding=opt.position_encoding,
					  feat_merge=opt.feat_merge,
					  feat_vec_exponent=opt.feat_vec_exponent,
					  feat_vec_size=opt.feat_vec_size,
					  dropout=opt.dropout,
					  word_padding_idx=word_padding_idx,
					  feat_padding_idx=feats_padding_idx,
					  word_vocab_size=num_word_embeddings,
					  feat_vocab_sizes=num_feat_embeddings,
					  sparse=opt.optim == "sparseadam")


def make_encoder(opt, embeddings):
	"""
	Various encoder dispatcher function.
	Args:
		opt: the option in current environment.
		embeddings (Embeddings): vocab embeddings for this encoder.
	"""
	if opt.encoder_type == "transformer":
		return TransformerEncoder(opt.enc_layers, opt.rnn_size,
								  opt.dropout, embeddings)
	elif opt.encoder_type == "cnn":
		return CNNEncoder(opt.enc_layers, opt.rnn_size,
						  opt.cnn_kernel_width,
						  opt.dropout, embeddings)
	elif opt.encoder_type == "mean":
		return MeanEncoder(opt.enc_layers, embeddings)
	else:
		# "rnn" or "brnn"
		return RNNEncoder(opt.rnn_type, opt.brnn, opt.enc_layers,
						  opt.rnn_size, opt.dropout, embeddings,
						  opt.bridge)


def make_decoder(opt, embeddings):
	"""
	Various decoder dispatcher function.
	Args:
		opt: the option in current environment.
		embeddings (Embeddings): vocab embeddings for this decoder.
	"""
	if opt.decoder_type == "transformer":
		return TransformerDecoder(opt.dec_layers, opt.rnn_size,
								  opt.global_attention, opt.copy_attn,
								  opt.dropout, embeddings)
	elif opt.decoder_type == "cnn":
		return CNNDecoder(opt.dec_layers, opt.rnn_size,
						  opt.global_attention, opt.copy_attn,
						  opt.cnn_kernel_width, opt.dropout,
						  embeddings)
	elif opt.input_feed:
		return InputFeedRNNDecoder(opt.rnn_type, opt.brnn,
								   opt.dec_layers, opt.rnn_size,
								   opt.global_attention,
								   opt.coverage_attn,
								   opt.context_gate,
								   opt.copy_attn,
								   opt.dropout,
								   embeddings,
								   opt.reuse_copy_attn)
	else:
		return StdRNNDecoder(opt.rnn_type, opt.brnn,
							 opt.dec_layers, opt.rnn_size,
							 opt.global_attention,
							 opt.coverage_attn,
							 opt.context_gate,
							 opt.copy_attn,
							 opt.dropout,
							 embeddings,
							 opt.reuse_copy_attn)

def make_context(opt, tgt_dict):
	padding_idx = tgt_dict.stoi[onmt.io.PAD_WORD]
	tok = [tgt_dict.stoi[onmt.io.PAD_WORD],
				onmt.io.UNK,
				tgt_dict.stoi[onmt.io.BOS_WORD],
				tgt_dict.stoi[onmt.io.EOS_WORD]]
	if opt.context_type is None:
		return None

	if opt.context_type == "HAN_join":
		return nn.ModuleList([HierarchicalContext(opt.rnn_size, opt.dropout, context_type=opt.context_type,context_size=opt.context_size, padding_idx=padding_idx, tok_idx=tok), HierarchicalContext(opt.rnn_size, opt.dropout, context_type=opt.context_type,context_size=opt.context_size, padding_idx=padding_idx, tok_idx=tok)])
	else:
		return HierarchicalContext(opt.rnn_size, opt.dropout, context_type=opt.context_type, 
						context_size=opt.context_size, padding_idx=padding_idx, tok_idx=tok)

def load_test_model(opt, dummy_opt):
	checkpoint = torch.load(opt.model,
							map_location=lambda storage, loc: storage)
	fields = onmt.io.load_fields_from_vocab(
		checkpoint['vocab'], data_type=opt.data_type)

	model_opt = checkpoint['opt']
	for arg in dummy_opt:
		if arg not in model_opt:
			model_opt.__dict__[arg] = dummy_opt[arg]

	model = make_base_model(model_opt, fields,
							use_gpu(opt), checkpoint)
	model.eval()
	model.generator.eval()
	return fields, model, model_opt


def make_base_model(model_opt, fields, gpu, checkpoint=None, train_part="all"):
	"""
	Args:
		model_opt: the option loaded from checkpoint.
		fields: `Field` objects for the model.
		gpu(bool): whether to use gpu.
		checkpoint: the model gnerated by train phase, or a resumed snapshot
					model from a stopped training.
	Returns:
		the NMTModel.
	"""
	assert model_opt.model_type in ["text", "img", "audio"], \
		("Unsupported model type %s" % (model_opt.model_type))

	# Make encoder.
	if model_opt.model_type == "text":
		src_dict = fields["src"].vocab
		feature_dicts = onmt.io.collect_feature_vocabs(fields, 'src')
		src_embeddings = make_embeddings(model_opt, src_dict,
										 feature_dicts)
		encoder = make_encoder(model_opt, src_embeddings)
	elif model_opt.model_type == "img":
		encoder = ImageEncoder(model_opt.enc_layers,
							   model_opt.brnn,
							   model_opt.rnn_size,
							   model_opt.dropout)
	elif model_opt.model_type == "audio":
		encoder = AudioEncoder(model_opt.enc_layers,
							   model_opt.brnn,
							   model_opt.rnn_size,
							   model_opt.dropout,
							   model_opt.sample_rate,
							   model_opt.window_size)

	# Make decoder.
	tgt_dict = fields["tgt"].vocab
	feature_dicts = onmt.io.collect_feature_vocabs(fields, 'tgt')
	tgt_embeddings = make_embeddings(model_opt, tgt_dict,
									 feature_dicts, for_encoder=False)

	# Share the embedding matrix - preprocess with share_vocab required.
	if model_opt.share_embeddings:
		# src/tgt vocab should be the same if `-share_vocab` is specified.
		if src_dict != tgt_dict:
			raise AssertionError('The `-share_vocab` should be set during '
								 'preprocess if you use share_embeddings!')

		tgt_embeddings.word_lut.weight = src_embeddings.word_lut.weight

	decoder = make_decoder(model_opt, tgt_embeddings)
	context = make_context(model_opt, tgt_dict)

	# Make NMTModel(= encoder + decoder).
	if model_opt.RISK_ratio > 0.0:
		scorer = onmt.translate.GNMTGlobalScorer(model_opt.alpha,model_opt.beta,model_opt.coverage_penalty,
												 model_opt.length_penalty)
		model = NMTModel(encoder, decoder, context, context_type=model_opt.context_type,tgt_vocab=fields['tgt'].vocab,
						 beam_size=model_opt.beam_size,n_best=model_opt.n_best,gpu=gpu,scorer=scorer,min_length=model_opt.min_length,
						 max_length=model_opt.max_length,stepwise_penalty=model_opt.stepwise_penalty,
						 block_ngram_repeat=model_opt.block_ngram_repeat,ignore_when_blocking=model_opt.ignore_when_blocking,
						 copy_attn=model_opt.copy_attn, context_size=model_opt.context_size)
	else:
		model = NMTModel(encoder, decoder, context, context_type=model_opt.context_type)
	model.model_type = model_opt.model_type

	# Make Generator.
	if not model_opt.copy_attn:
		generator = nn.Sequential(
			nn.Linear(model_opt.rnn_size, len(fields["tgt"].vocab)),
			nn.LogSoftmax(dim=1))
		if model_opt.share_decoder_embeddings:
			generator[0].weight = decoder.embeddings.word_lut.weight
	else:
		generator = CopyGenerator(model_opt.rnn_size,
								  fields["tgt"].vocab)

	# Load the model states from checkpoint or initialize them.
	if checkpoint is not None:
		print('Loading model parameters.')
		model_dict = checkpoint['model']
		if train_part == "context":
			model_dict = model.state_dict()
			if 'join' in model_opt.context_type: 
				pretrained_dict = {}
				for k, v in checkpoint['model'].items():
					if k in model_dict:
						if 'doc_context' in k:
							k = k.replace('doc_context', 'doc_context.0')
						pretrained_dict[k]=v
			else:
				pretrained_dict = {k: v for k, v in checkpoint['model'].items() if k in model_dict and 'doc_context' not in k}
			model_dict.update(pretrained_dict)

		model.load_state_dict(model_dict, strict=False)
		generator.load_state_dict(checkpoint['generator'])
		if train_part == "context":
			print ("Freezing parameters of main model")
			for param in model.parameters():
				param.require_grad = False
			for param in generator.parameters():
				param.require_grad = False
			print ("Unfreezing parameters of context")
			for param in model.doc_context.parameters():
				param.require_grad = True
				if model_opt.param_init != 0.0:
					param.data.uniform_(-model_opt.param_init, model_opt.param_init)
				if model_opt.param_init_glorot:
					if param.dim() > 1:
						xavier_uniform(param)
	else:
		if model_opt.param_init != 0.0:
			print('Intializing model parameters.')
			for p in model.parameters():
				p.data.uniform_(-model_opt.param_init, model_opt.param_init)
			for p in generator.parameters():
				p.data.uniform_(-model_opt.param_init, model_opt.param_init)
		if model_opt.param_init_glorot:
			for p in model.parameters():
				if p.dim() > 1:
					xavier_uniform(p)
			for p in generator.parameters():
				if p.dim() > 1:
					xavier_uniform(p)

		if hasattr(model.encoder, 'embeddings'):
			model.encoder.embeddings.load_pretrained_vectors(
					model_opt.pre_word_vecs_enc, model_opt.fix_word_vecs_enc)
		if hasattr(model.decoder, 'embeddings'):
			model.decoder.embeddings.load_pretrained_vectors(
					model_opt.pre_word_vecs_dec, model_opt.fix_word_vecs_dec)


	# Add generator to model (this registers it as parameter of model).
	model.generator = generator

	# Make the whole model leverage GPU if indicated to do so.
	if gpu:
		model.cuda()
	else:
		model.cpu()

	return model
