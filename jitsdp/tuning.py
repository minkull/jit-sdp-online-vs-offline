# coding=utf-8
from jitsdp.data import make_stream
from jitsdp.evaluation import prepare_train_data
from jitsdp.utils import filename_to_path

import argparse
import itertools
from joblib import Memory
import numpy as np
from hyperopt import hp
from hyperopt.pyll.base import scope
import hyperopt.pyll.stochastic as config_space_sampler

memory = Memory(location='logs', verbose=0)

MAX_BORB_SAMPLE_SIZE_START = 1000
MAX_BORB_SAMPLE_SIZE_END = 8000
MAX_BORB_SAMPLE_SIZE_STEP = 1000


class Experiment():

    def __init__(self, experiment_config, seed_dataset_configs, models_configs):
        self.meta_model = experiment_config['meta-model']
        self.experiment_config = experiment_config
        self.seed_dataset_configs = seed_dataset_configs
        self.models_configs = models_configs

    def remove_meta_model(self, config):
        config = dict(config)
        del config['meta-model']
        return config

    @property
    def name(self):
        rate_driven = 'r' if self.experiment_config['rate-driven'] else ''
        model = self.experiment_config['model']
        train_data = 'cp' if self.experiment_config['cross-project'] else 'wp'
        return '{}{}-{}-{}'.format(rate_driven, self.meta_model, model, train_data)

    def to_configs(self):
        configs = []
        for models_config in self.models_configs:
            for seed_dataset_config in self.seed_dataset_configs:
                config = dict()
                config.update(self.experiment_config)
                config.update(models_config)
                config.update(seed_dataset_config)
                config = self.add_start(config)
                config = self.fix_borb_max_sample_size(config)
                configs.append(config)
        return configs

    def to_shell(self, out):
        for config in self.to_configs():
            config = self.remove_meta_model(config)
            params = ['--{} {}'.format(key, value)
                      for key, value in config.items()]
            params = ' '.join(params)
            out.write(
                './jitsdp {} {}\n'.format(self.meta_model, params))

    def add_start(self, config):
        config = dict(config)
        config['end'] = 1000 if config['cross-project'] else 5000
        return config

    def fix_borb_max_sample_size(self, config):
        if self.meta_model != 'borb':
            return config
        config = dict(config)
        relevant_keys = set(['dataset', 'end', 'cross-project',
                             'borb-waiting-time', 'borb-max-sample-size'])
        relevant_config = {key: value for (
            key, value) in config.items() if key in relevant_keys}
        config['borb-max-sample-size'] = _scale_max_sample_size(
            relevant_config)
        return config


@memory.cache
def _scale_max_sample_size(config):
    undescore_config = {key.replace(
        '-', '_'): value for (key, value) in config.items()}
    undescore_config['uncertainty'] = False
    df_commits = make_stream(undescore_config['dataset'])
    df_train = df_commits[:undescore_config['end']]
    df_train = prepare_train_data(df_train, undescore_config)
    max_train_size = min(len(df_train), MAX_BORB_SAMPLE_SIZE_END)
    assert max_train_size >= MAX_BORB_SAMPLE_SIZE_START
    fixed_borb_max_sample_size = (undescore_config['borb_max_sample_size'] - MAX_BORB_SAMPLE_SIZE_START) / (
        MAX_BORB_SAMPLE_SIZE_END - MAX_BORB_SAMPLE_SIZE_START)
    fixed_borb_max_sample_size = fixed_borb_max_sample_size * \
        (max_train_size - MAX_BORB_SAMPLE_SIZE_START) + \
        MAX_BORB_SAMPLE_SIZE_START
    fixed_borb_max_sample_size = int(round(
        fixed_borb_max_sample_size / MAX_BORB_SAMPLE_SIZE_STEP) * MAX_BORB_SAMPLE_SIZE_STEP)
    return fixed_borb_max_sample_size


def add_arguments(parser, filename):
    parser.add_argument('--start',   type=int,
                        help='Starting index of the random configurations slice.', required=True)
    parser.add_argument('--end',   type=int,
                        help='Stopping index of the random configurations slice.', required=True)
    parser.add_argument('--cross-project',   type=int,
                        help='Whether must use cross-project data.', required=True, choices=[0, 1])
    parser.add_argument('--filename',   type=str,
                        help='Output script path.', default=filename)
    parser.add_argument('--no-validation',
                        help='Disable validations of the data the flows from hyperparameter tuning to testing.', action='store_true')


def generate(config):
    # experiments
    cross_project = config['cross_project']
    orb_rorb_grid = {
        'meta-model': ['orb'],
        'cross-project': [0, 1] if cross_project else [0],
        'rate-driven': [0, 1],
        'model': ['hts'],
    }
    borb_rborb_grid = {
        'meta-model': ['borb'],
        'cross-project': [0, 1] if cross_project else [0],
        'rate-driven': [0, 1],
        'model': ['ihf'],
    }
    rborb_grid = {
        'meta-model': ['borb'],
        'cross-project': [0, 1] if cross_project else [0],
        'rate-driven': [1],
        'model': ['lr', 'mlp', 'nb', 'irf'],
    }
    experiment_configs = [
        orb_rorb_grid,
        borb_rborb_grid,
        rborb_grid,
    ]
    # seeds and datasets
    experiment_configs = map(grid_to_configs, experiment_configs)
    experiment_configs = itertools.chain.from_iterable(experiment_configs)
    seed_dataset_configs = {
        'dataset': ['brackets', 'camel', 'fabric8', 'jgroups', 'neutron', 'tomcat', 'broadleaf', 'nova', 'npm', 'spring-integration'],
        'seed': [0, 1, 2, 3, 4],
    }
    seed_dataset_configs = grid_to_configs(seed_dataset_configs)
    # meta-models and models
    models_configs = create_models_configs(config)
    file_ = filename_to_path(config['filename'])
    with open(file_, mode='w') as out:
        for experiment in configs_to_experiments(experiment_configs, seed_dataset_configs, models_configs):
            experiment.to_shell(out)


def configs_to_experiments(experiment_configs, seed_dataset_configs, models_configs):
    for experiment_config in experiment_configs:
        model = experiment_config['model']
        experiment = Experiment(experiment_config=experiment_config,
                                seed_dataset_configs=seed_dataset_configs, models_configs=models_configs[model])
        yield experiment


def grid_to_configs(grid):
    keys = grid.keys()
    values_lists = grid.values()
    values_tuples = itertools.product(*values_lists)
    return list(map(lambda values_tuple: dict(zip(keys, list(values_tuple))), values_tuples))


def create_models_configs(config):
    meta_model_shared = meta_model_shared_config_space()
    orb = {}
    orb.update(meta_model_shared['orb'])
    orb.update([loguniform('orb-decay-factor', .9, .999),
                uniform('orb-n', 3, 7, 2),
                uniform('orb-rd-grace-period', 100, 500, 100),
                ])

    hoeffding_shared = hoeffding_shared_config_space()
    hts = {}
    hts.update(orb)
    hts.update(hoeffding_shared['hts'])

    borb = {}
    borb.update(meta_model_shared['borb'])
    borb.update([uniform('borb-pull-request-size', 50, 200, 50),
                 loguniform('borb-max-sample-size',
                            MAX_BORB_SAMPLE_SIZE_START, MAX_BORB_SAMPLE_SIZE_END),
                 ])

    ihf = {}
    ihf.update(borb)
    ihf.update(hoeffding_shared['ihf'])

    lr = {}
    lr.update(borb)
    lr.update([
        loguniform('lr-alpha', .01, 1.),
        uniform('lr-n-epochs',  10, 80, 10),
        loguniform('lr-batch-size', 128, 512, 128),
    ])

    mlp = {}
    mlp.update(borb)
    mlp.update([
        loguniform('mlp-learning-rate', .0001, .01),
        uniform('mlp-n-epochs', 10, 80, 10),
        uniform('mlp-n-hidden-layers', 1, 3, 1),
        uniform('mlp-hidden-layers-size', 5, 15, 2),
        uniform('mlp-dropout-input-layer', .1, .3, .1),
        uniform('mlp-dropout-hidden-layer', .3, .5, .1),
        loguniform('mlp-batch-size',  128, 512, 128),
    ])

    nb = {}
    nb.update(borb)
    nb.update([
        uniform('nb-n-updates', 10, 80, 10),
    ])

    irf = {}
    irf.update(borb)
    irf.update([
        uniform('irf-n-estimators', 20, 100, 20),
        choiceuniform('irf-criterion', ['gini', 'entropy']),
        uniform('irf-min-samples-leaf', 100, 300,  100),
        uniform('irf-max-features', 3, 7, 2),
    ])

    start = config['start']
    end = config['end']
    models_configs = {'hts': config_space_to_configs(hts, start, end),
                      'ihf': config_space_to_configs(ihf, start, end),
                      'lr': config_space_to_configs(lr, start, end),
                      'mlp': config_space_to_configs(mlp, start, end),
                      'nb': config_space_to_configs(nb, start, end),
                      'irf': config_space_to_configs(irf, start, end),
                      }

    return models_configs


def uniform(name, start, end, step=None):
    if step is None:
        return (name, converter(start, hp.uniform(name, start, end)))
    else:
        return (name, converter(start, start + hp.quniform(name, 0, end - start, step)))


def loguniform(name, start, end, step=None):
    if step is None:
        return (name, converter(start, hp.loguniform(name, np.log(start), np.log(end))))
    else:
        return (name, converter(start, start + hp.qloguniform(name, 0, np.log(end) - np.log(start), step)))


def converter(sample, apply):
    if int == type(sample):
        return scope.int(apply)
    else:
        return apply


def choiceuniform(name, options):
    return (name, hp.choice(name, options))


def meta_model_shared_config_space():
    config_spaces = {}
    meta_models = ['orb', 'borb']
    for meta_model in meta_models:
        config_spaces[meta_model] = [
            uniform('{}-waiting-time'.format(meta_model), 90, 180, 30),
            uniform('{}-ma-window-size'.format(meta_model), 50, 200, 50),
            uniform('{}-th'.format(meta_model), .3, .5, .05),
            loguniform('{}-l0'.format(meta_model), 1., 20.),
            loguniform('{}-l1'.format(meta_model), 1., 20.),
            uniform('{}-m'.format(meta_model), 1.1, np.e, .2),
        ]
    return config_spaces


def hoeffding_shared_config_space():
    config_spaces = {}
    models = ['hts', 'ihf']
    for model in models:
        config_spaces[model] = [
            uniform('{}-n-estimators'.format(model), 10, 40, 10),
            uniform('{}-grace-period'.format(model), 100, 500, 100),
            choiceuniform('{}-split-criterion'.format(model),
                          ['gini', 'info_gain', 'hellinger']),
            loguniform('{}-split-confidence'.format(model), 0.0000001, 0.5),
            uniform('{}-tie-threshold'.format(model), 0.05, 0.5),
            # use only False (default) to avoid bug when value is True
            #choiceuniform('{}-remove-poor-atts'.format(model), [1, 0]),
            choiceuniform('{}-no-preprune'.format(model), [1, 0]),
            choiceuniform('{}-leaf-prediction'.format(model),
                          ['mc', 'nb', 'nba']),
        ]
    return config_spaces


def config_space_to_configs(config_space, start, end):
    rng = np.random.RandomState(seed=0)
    configs = [config_space_sampler.sample(
        config_space, rng=rng) for i in range(end - start)]
    return configs[start:end]
